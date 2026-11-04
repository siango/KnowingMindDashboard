"""V3 QC tests: risk selection, failure detection, bounded repair."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import qc  # noqa: E402
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.receipts import write_receipt  # noqa: E402
from coding_worker_v2.tasks import Task  # noqa: E402


def done_task(repo: str, test_rc=0, changed=None, complexity="S"):
    task = Task.from_dict({
        "mission_id": "M", "task_id": "q1", "owner_node": "AV15",
        "input": {"repo": repo, "goal": "g", "test_command": "python3 -m unittest",
                  "hints": {"acceptance": ["ok"], "complexity": complexity}}})
    task.status = "DONE"
    task.output = {"changed": changed or ["a.py"], "patch_digest": "test-patch"}
    task.evidence.append({"event": "tests_run",
                          "tests": {"ran": True, "rc": test_rc,
                                    "duration_ms": 5, "output_tail": "ok"}})
    return task


class TestQC(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "a.py").write_text("x = 1\n")
        (self.repo / "test_a.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            " def test_x(self):\n  self.assertEqual(1, 1)\n")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.repo,
                       check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.repo,
                       check=True)
        source_sha = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.ctx = {"config": WorkerConfig.from_env(),
                    "store": QueueStore(self.root / "q"),
                    "workspace": str(self.repo), "repo": str(self.repo),
                    "mission_audit": {"stage": "BEFORE_CLOSE",
                                      "gate_passed": True},
                    "executor_id": "AV15",
                    "executor_runner_id": "runner-exec",
                    "source_sha": source_sha,
                    "independent_verification": {
                        "verifier_id": "independent-ci",
                        "runner_id": "runner-qc",
                        "status": "PASS", "source_sha": source_sha,
                        "patch_digest": "test-patch",
                        "evidence_refs": ["test-receipt"]}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_risk_tiers(self):
        self.assertIn("regression", qc.select_checks(3))
        self.assertNotIn("regression", qc.select_checks(1))
        self.assertNotIn("integration_tests", qc.select_checks(0))
        self.assertIn("security", qc.select_checks(0))

    def test_risk_score(self):
        task = done_task(str(self.repo), complexity="L")
        task.task_input = task.task_input
        self.assertGreaterEqual(qc.risk_score(task, ["1", "2", "3", "4"]), 3)

    def test_skipped_mandatory_check_is_not_a_pass(self):
        task = done_task(str(self.repo))
        with mock.patch.dict(qc.CHECKS, {
                "mandatory": lambda _task, _ctx: qc._check(
                    "mandatory", qc.SKIPPED, "required evidence unavailable")}):
            report = qc.run_qc(task, self.ctx, only=["mandatory"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["failed"], ["mandatory"])

    def test_requirements_need_passing_test_evidence(self):
        task = done_task(str(self.repo))
        task.evidence.clear()
        report = qc.run_qc(task, self.ctx, only=["requirements"])
        self.assertFalse(report["passed"])
        self.assertIn("test evidence", report["checks"][0]["detail"])

    def test_source_integrity_does_not_pass_without_git_revision(self):
        no_git_repo = self.root / "no-git"
        no_git_repo.mkdir()
        task = done_task(str(no_git_repo))
        report = qc.run_qc(task, {**self.ctx, "repo": str(no_git_repo)},
                           only=["source_integrity"])
        self.assertFalse(report["passed"])

    def test_independent_verification_rejects_executor_self_review(self):
        task = done_task(str(self.repo))
        ctx = {**self.ctx,
               "independent_verification": {
                   **self.ctx["independent_verification"],
                   "verifier_id": "AV15"}}
        report = qc.run_qc(task, ctx, only=["independent_verification"])
        self.assertFalse(report["passed"])

    def test_independent_verification_requires_authenticated_proof(self):
        task = done_task(str(self.repo))
        proof = {**self.ctx["independent_verification"],
                 "authenticated": False, "signature_sha256": "a" * 64}
        report = qc.run_qc(
            task, {**self.ctx, "independent_verification": proof},
            only=["independent_verification"])
        self.assertFalse(report["passed"])
        self.assertIn("authenticated", report["checks"][0]["detail"])

    def test_mission_auditor_unavailable_is_a_qc_failure(self):
        from coding_worker_v2.agent.auditor import MissionAuditorError

        task = done_task(str(self.repo))
        ctx = {**self.ctx}
        ctx.pop("mission_audit")
        with mock.patch(
                "coding_worker_v2.agent.auditor.run_mission_audit",
                side_effect=MissionAuditorError("AUDITOR_UNAVAILABLE")):
            report = qc.run_qc(task, ctx, only=["mission_audit"])
        self.assertFalse(report["passed"])
        self.assertIn("mission_audit", report["failed"])

    def test_unit_fail_detected(self):
        task = done_task(str(self.repo), test_rc=1)
        report = qc.run_qc(task, self.ctx, only=["unit_tests"])
        self.assertFalse(report["passed"])
        self.assertIn("unit_tests", report["failed"])

    def test_security_secret_detected(self):
        (self.repo / "a.py").write_text('api_key = "sk-live-123"\n')
        task = done_task(str(self.repo))
        report = qc.run_qc(task, self.ctx, only=["security"])
        self.assertFalse(report["passed"])

    def test_security_shell_detected(self):
        (self.repo / "a.py").write_text("import subprocess\n"
                                        "subprocess.run('x', shell=True)\n")
        task = done_task(str(self.repo))
        report = qc.run_qc(task, self.ctx, only=["security"])
        self.assertFalse(report["passed"])

    def test_security_clean_passes(self):
        task = done_task(str(self.repo))
        report = qc.run_qc(task, self.ctx, only=["security"])
        self.assertTrue(report["passed"])

    def test_syntax_fail_detected(self):
        (self.repo / "a.py").write_text("def broken(:\n")
        task = done_task(str(self.repo))
        report = qc.run_qc(task, self.ctx, only=["syntax"])
        self.assertFalse(report["passed"])

    def test_integration_rerun_passes(self):
        task = done_task(str(self.repo))
        report = qc.run_qc(task, self.ctx, only=["integration_tests"])
        self.assertTrue(report["passed"])

    def test_receipt_readback(self):
        task = done_task(str(self.repo))
        with mock.patch.dict(os.environ, {
                "KMW2_STATE_DIR": str(self.root / "state")}):
            receipt_path = write_receipt(self.root / "receipts", task)
        report = qc.run_qc(
            task, {**self.ctx, "receipt_path": str(receipt_path)},
            only=["receipt_readback"])
        self.assertTrue(report["passed"])

    def test_repair_cycle_escalates(self):
        task = done_task(str(self.repo), test_rc=1)
        calls = []

        def repair_fn(t, summary):
            calls.append(summary)
            return t  # repair changes nothing: still failing

        with mock.patch.object(qc, "select_checks", return_value=["unit_tests"]):
            report = qc.qc_repair_cycle(task, self.ctx, repair_fn, max_repairs=1)
        self.assertFalse(report["passed"])
        self.assertTrue(report["escalated"])
        self.assertEqual(len(calls), 1)

    def test_repair_cycle_recovers(self):
        task = done_task(str(self.repo), test_rc=1)

        def repair_fn(t, summary):
            _ = summary
            t.evidence = [{"event": "tests_run",
                           "tests": {"ran": True, "rc": 0,
                                     "duration_ms": 5, "output_tail": "ok"}}]
            return t

        with mock.patch.object(qc, "select_checks", return_value=["unit_tests"]), \
                mock.patch.object(qc, "run_qc", wraps=qc.run_qc) as spy:
            report = qc.qc_repair_cycle(task, {**self.ctx}, repair_fn,
                                        max_repairs=1)
        self.assertTrue(report["passed"])
        self.assertEqual(spy.call_count, 2)


if __name__ == "__main__":
    unittest.main()
