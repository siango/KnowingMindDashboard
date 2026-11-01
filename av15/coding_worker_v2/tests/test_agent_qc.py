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
from coding_worker_v2.tasks import Task  # noqa: E402


def done_task(repo: str, test_rc=0, changed=None, complexity="S"):
    task = Task.from_dict({
        "mission_id": "M", "task_id": "q1", "owner_node": "AV15",
        "input": {"repo": repo, "goal": "g", "test_command": "python3 -m unittest",
                  "hints": {"acceptance": ["ok"], "complexity": complexity}}})
    task.status = "DONE"
    task.output = {"changed": changed or ["a.py"]}
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
        self.ctx = {"config": WorkerConfig.from_env(),
                    "store": QueueStore(self.root / "q"),
                    "workspace": str(self.repo), "repo": str(self.repo)}

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
        report = qc.run_qc(task, self.ctx, only=["receipt_readback"])
        self.assertTrue(report["passed"])

    def test_repair_cycle_escalates(self):
        task = done_task(str(self.repo), test_rc=1)
        calls = []

        def repair_fn(t, summary):
            calls.append(summary)
            return t  # repair changes nothing: still failing

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

        with mock.patch.object(qc, "run_qc",
                               wraps=qc.run_qc) as spy:
            report = qc.qc_repair_cycle(task, {**self.ctx}, repair_fn,
                                        max_repairs=1)
        self.assertTrue(report["passed"])
        self.assertEqual(spy.call_count, 2)


if __name__ == "__main__":
    unittest.main()
