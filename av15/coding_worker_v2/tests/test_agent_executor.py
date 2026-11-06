"""V3 executor tests: dedup-aware plan execution over a fake backend."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import executor, planner  # noqa: E402
from coding_worker_v2.agent.auditor import (SCOPE_DIGEST,  # noqa: E402
                                            context_digest)
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.tests import FakeBackend, FakeState  # noqa: E402

FIX = json.dumps({"edits": [{"path": "calc.py", "old": "return a - b",
                              "new": "return a + b"}]})


def fixture_repo(root: Path) -> str:
    repo = root / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n"
        "class T(unittest.TestCase):\n def test_add(self):\n"
        "  self.assertEqual(add(2, 3), 5)\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                   check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo,
                   check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    return str(repo)


def config_for(port: int) -> WorkerConfig:
    with mock.patch.dict(os.environ, {
            "KMW2_OLLAMA_URL": f"http://127.0.0.1:{port}",
            "KMW2_ALLOW_ECHO_FALLBACK": "0",
            "KMW2_DEEPSEEK_API_ENABLED": "0",
            "KMW2_SPENDING_BUDGET_USD": "0"}, clear=False):
        for key in ("KMW2_QWEN_MODEL", "KMW2_QWEN_FALLBACK_MODEL",
                    "KMW2_DEEPSEEK_API_KEY"):
            os.environ.pop(key, None)
        return WorkerConfig.from_env()


def simple_plan_for(command: str) -> planner.Plan:
    plan = planner.simple_plan(command, "KM-TEST", "ptest")
    plan.tasks[0].allowed_paths = ["calc.py"]
    return plan


class TestExecutor(FakeBackend):
    def setUp(self):
        FakeState.chat_queue = []
        FakeState.chat_content = FIX
        self.audit_stages = []
        self.execution_events = []
        self.close_context = None

        def audit(stage, context):
            self.audit_stages.append(stage)
            self.execution_events.append(stage)
            if stage == "BEFORE_CLOSE":
                self.close_context = context
            return {"stage": stage, "gate_passed": True,
                    "context_digest": context_digest(stage, context)}

        audit_patch = mock.patch.object(executor, "run_mission_audit",
                                        side_effect=audit)
        audit_patch.start()
        self.addCleanup(audit_patch.stop)
        def qc(_task, _context):
            self.execution_events.append("QC")
            return {"passed": True, "independent_verified": True, "checks": []}

        qc_patch = mock.patch("coding_worker_v2.agent.qc.run_qc", side_effect=qc)
        qc_patch.start()
        self.addCleanup(qc_patch.stop)

    @staticmethod
    def verified_proof(request_path, _proof_path, _public_key, lease_valid):
        assert lease_valid()
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        return {
            **request["request"],
            "request_digest": request["request_digest"],
            "verifier_id": "independent-ci", "runner_id": "runner-qc",
            "status": "PASS", "evidence_refs": ["test:independent"],
            "authenticated": True, "signature_sha256": "a" * 64,
        }

    def test_qwen_chain_muse_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = config_for(self.port)
            chain = executor.qwen_chain(config)
            self.assertEqual(len(chain), 1)
            self.assertEqual(chain[0].name, "qwen-local")

    def test_qwen_chain_empty_when_down(self):
        with mock.patch.dict(os.environ,
                             {"KMW2_OLLAMA_URL": "http://127.0.0.1:9"},
                             clear=False):
            config = WorkerConfig.from_env()
        self.assertEqual(executor.qwen_chain(config), [])

    def test_backoff_bounded(self):
        self.assertEqual(executor.backoff_s(0), 1)
        self.assertEqual(executor.backoff_s(10), 60)

    def test_new_task_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}), \
                    mock.patch.object(executor, "wait_signed_proof",
                                      side_effect=self.verified_proof):
                report = executor.execute_plan(
                    simple_plan_for("fix add() so 2+3==5"), config,
                    QueueStore(root / "q"), root / "receipts", root / "state",
                    repo, use_worktree=False, sleep_fn=lambda s: None)
            self.assertEqual(report["status"], "DONE")
            self.assertEqual(report["tasks"][0]["decision"], "NEW")
            self.assertEqual(report["tasks"][0]["status"], "DONE")
            receipts = list((root / "receipts").glob("*.json"))
            self.assertEqual(len(receipts), 1)
            self.assertEqual(self.audit_stages,
                             ["ADMIT", "BEFORE_MUTATION", "AFTER_TEST",
                              "BEFORE_CLOSE"])
            self.assertEqual(self.execution_events,
                             ["ADMIT", "BEFORE_MUTATION", "AFTER_TEST",
                              "QC", "BEFORE_CLOSE"])
            self.assertTrue(Path(self.close_context["receipt_path"]).is_file())
            self.assertTrue(self.close_context["qc_report"]["passed"])
            self.assertTrue(
                self.close_context["independent_verification"]["authenticated"])

    def test_queue_is_not_done_if_final_receipt_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            store = QueueStore(root / "q")
            write_receipt = executor.write_receipt

            def fail_done_receipt(directory, task, extra=None):
                if task.status == "DONE":
                    raise OSError("receipt storage unavailable")
                return write_receipt(directory, task, extra)

            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}), \
                    mock.patch.object(executor, "wait_signed_proof",
                                      side_effect=self.verified_proof), \
                    mock.patch.object(executor, "write_receipt",
                                      side_effect=fail_done_receipt):
                with self.assertRaisesRegex(OSError, "receipt storage unavailable"):
                    executor.execute_plan(
                        simple_plan_for("fix add() so 2+3==5"), config, store,
                        root / "receipts", root / "state", repo,
                        use_worktree=False, sleep_fn=lambda s: None)
            self.assertEqual(store.health()["done"], 0)
            self.assertEqual(store.health()["doing"], 1)
            self.assertEqual(store.health()["failed"], 0)

    def test_auditor_unavailable_blocks_before_any_queue_or_repo_change(self):
        from coding_worker_v2.agent.auditor import MissionAuditorError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            store = QueueStore(root / "q")
            with mock.patch.object(
                    executor, "run_mission_audit",
                    side_effect=MissionAuditorError("AUDITOR_UNAVAILABLE")):
                report = executor.execute_plan(
                    simple_plan_for("fix add() so 2+3==5"), config, store,
                    root / "receipts", root / "state", repo,
                    use_worktree=False)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["next_action"], "AUDITOR_UNAVAILABLE")
            self.assertEqual(store.health()["inbox"], 0)
            self.assertEqual(store.health()["doing"], 0)
            self.assertEqual(store.health()["done"], 0)

    def test_missing_approved_file_list_blocks_before_enqueue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            store = QueueStore(root / "q")
            plan = simple_plan_for("fix add() so 2+3==5")
            plan.tasks[0].allowed_paths = []
            report = executor.execute_plan(
                plan, config, store, root / "receipts", root / "state", repo,
                use_worktree=False)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["tasks"][0]["reason"],
                             "APPROVED_FILE_ALLOWLIST_REQUIRED")
            self.assertEqual(store.health()["inbox"], 0)
            self.assertEqual(store.health()["doing"], 0)

    def test_unimplemented_model_or_tool_dispatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            for model, tools in (("muse-cli", []), ("qwen-local", ["repo_inspect"])):
                plan = simple_plan_for("fix add() so 2+3==5")
                plan.tasks[0].model = model
                plan.tasks[0].tools = tools
                store = QueueStore(root / f"q-{model}-{len(tools)}")
                report = executor.execute_plan(
                    plan, config, store, root / "receipts", root / "state", repo,
                    use_worktree=False)
                self.assertEqual(report["status"], "BLOCKED")
                self.assertEqual(store.health()["inbox"], 0)
                self.assertEqual(store.health()["doing"], 0)

    def test_duplicate_truncated_task_targets_block_before_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            plan = planner.validate_plan_dict({
                "schema": "KM_TASK_PLAN_V1", "mission_id": "M", "plan_id": "ptest",
                "owner_node": "AV15", "source_command": "duplicate targets",
                "tasks": [
                    {"task_id": "a" * 119 + "x", "goal": "first",
                     "acceptance": ["ok"], "allowed_paths": ["calc.py"]},
                    {"task_id": "a" * 119 + "y", "goal": "second",
                     "acceptance": ["ok"], "allowed_paths": ["calc.py"]},
                ]})
            store = QueueStore(root / "q")
            report = executor.execute_plan(
                plan, config, store, root / "receipts", root / "state", repo,
                use_worktree=False)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["next_action"], "RESOLVE_DUPLICATE_TARGETS")
            self.assertEqual(store.health()["inbox"], 0)
            self.assertEqual(self.audit_stages, [])

    def test_qc_failure_never_completes_queue_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            store = QueueStore(root / "q")
            qc_calls = []
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}):
                report = executor.execute_plan(
                    simple_plan_for("fix add() so 2+3==5"), config, store,
                    root / "receipts", root / "state", repo, use_worktree=False,
                    qc_fn=lambda _task, _ctx: qc_calls.append(True) or {
                        "passed": False, "failed": ["independent_check"]},
                    sleep_fn=lambda s: None)
            self.assertTrue(qc_calls, report)
            self.assertEqual(report["status"], "FAILED")
            self.assertEqual(store.health()["done"], 0)
            self.assertEqual(store.health()["failed"], 1)

    def test_qc_without_independent_verifier_never_completes_queue_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            store = QueueStore(root / "q")
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}):
                report = executor.execute_plan(
                    simple_plan_for("fix add() so 2+3==5"), config, store,
                    root / "receipts", root / "state", repo, use_worktree=False,
                    qc_fn=lambda _task, _ctx: {
                        "passed": True, "checks": []},
                    sleep_fn=lambda s: None)
            self.assertEqual(report["status"], "FAILED")
            self.assertEqual(store.health()["done"], 0)
            self.assertEqual(store.health()["failed"], 1)

    def test_reuse_skips_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            qc = {"passed": True, "independent_verified": True, "checks": []}
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}), \
                    mock.patch.object(executor, "wait_signed_proof",
                                      side_effect=self.verified_proof):
                first = executor.execute_plan(
                    simple_plan_for("fix add() so 2+3==5"), config,
                    QueueStore(root / "q"), root / "receipts", root / "state",
                    repo, use_worktree=False, qc_fn=lambda r, c: qc,
                    sleep_fn=lambda s: None)
                self.assertEqual(first["status"], "DONE")
                # Break the model: REUSE must not call it again.
                FakeState.chat_content = "garbage {{{"
                second = executor.execute_plan(
                    simple_plan_for("fix add() so 2+3==5"), config,
                    QueueStore(root / "q"), root / "receipts", root / "state",
                    repo, use_worktree=False, qc_fn=lambda r, c: qc,
                    sleep_fn=lambda s: None)
            self.assertEqual(second["tasks"][0]["decision"], "REUSE")
            self.assertEqual(second["reused"], 1)

    def test_dep_failure_blocks_dependent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            plan = planner.validate_plan_dict({
                "schema": "KM_TASK_PLAN_V1", "mission_id": "M", "plan_id": "pd",
                "owner_node": "AV15", "source_command": "x",
                "tasks": [
                    {"task_id": "a", "goal": "missing repo task",
                     "acceptance": ["ok"]},
                    {"task_id": "b", "goal": "depends on a", "deps": ["a"],
                     "acceptance": ["ok"]}]})
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}):
                report = executor.execute_plan(
                    plan, config, QueueStore(root / "q"), root / "receipts",
                    root / "state", str(root / "nothere"),
                    use_worktree=False, sleep_fn=lambda s: None)
        by_id = {t["task_id"]: t for t in report["tasks"]}
        self.assertEqual(by_id["a"]["decision"], "BLOCKED")
        self.assertEqual(by_id["b"]["decision"], "BLOCKED")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["next_action"], "RESOLVE_BLOCKED_TASKS")

    def test_partial_plan_is_not_reported_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = config_for(self.port)
            plan = planner.validate_plan_dict({
                "schema": "KM_TASK_PLAN_V1", "mission_id": "M", "plan_id": "pd",
                "owner_node": "AV15", "source_command": "x",
                "tasks": [{"task_id": "a", "goal": "blocked task",
                           "acceptance": ["ok"]}]})
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}):
                report = executor.execute_plan(
                    plan, config, QueueStore(root / "q"), root / "receipts",
                    root / "state", str(root / "nothere"),
                    use_worktree=False, sleep_fn=lambda s: None)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["skipped"], 1)
        self.assertNotEqual(report["next_action"], "REVIEW_RECEIPTS")

    def test_test_process_receives_scrubbed_environment(self):
        from coding_worker_v2 import run_once
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            with mock.patch.dict(os.environ, {
                    "KMW2_STATE_DIR": tmp,
                    "KMW2_TEST_SECRET_TOKEN": "probe-value"}):
                with mock.patch.object(
                        run_once, "run_tests",
                        return_value={"ran": True, "rc": 0, "duration_ms": 1,
                                      "output": ""}) as tests:
                    with mock.patch.object(
                            executor, "wait_signed_proof",
                            side_effect=self.verified_proof):
                        report = executor.execute_plan(
                            simple_plan_for("fix add() so 2+3==5"), config,
                            QueueStore(root / "q"), root / "receipts",
                            root / "state", repo, use_worktree=False,
                            sleep_fn=lambda s: None)
            self.assertEqual(report["status"], "DONE")
            child_env = tests.call_args.kwargs["env"]
            self.assertNotIn("KMW2_TEST_SECRET_TOKEN", child_env)

    def test_history_injected(self):
        from coding_worker_v2.run_once import run_task
        from coding_worker_v2.tasks import Task
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            store = QueueStore(root / "q")
            task = Task.from_dict({
                "mission_id": "M", "task_id": "h1", "owner_node": "AV15",
                "input": {"repo": repo, "goal": "fix add()",
                          "test_command": "python3 -m unittest",
                          "hints": {
                              "plan_id": "h1",
                              "plan_digest": "0" * 64,
                              "scope_digest": "sha256:" + "0" * 64,
                              "base_commit": "",
                              "acceptance": ["task goal implemented"],
                              "approved_scope": {
                                  "mission_id": "M", "plan_id": "h1",
                                  "revision": 1, "owner_node": "AV15",
                                  "queue_task_id": "h1",
                                  "source_command": "fix add()",
                                  "plan_digest": "0" * 64,
                                  "scope_digest": SCOPE_DIGEST,
                                  "task_id": "h1", "goal": "fix add()",
                                  "acceptance": ["task goal implemented"],
                                  "base_commit": "",
                                  "allowed_paths": ["calc.py"]}}}})
            store.enqueue(task)
            claimed = store.claim()
            assert claimed is not None
            history = [{"role": "user", "content": "previous turn"},
                       {"role": "assistant", "content": "ack"}]
            result = run_task(claimed, store, config, root / "spend.json",
                              chain=executor.qwen_chain(config),
                              history=history)
            self.assertEqual(result.status, "DONE")
            gens = [e for e in result.evidence
                    if e.get("event") == "model_generated"]
            self.assertEqual(gens[0]["history_turns"], 2)


if __name__ == "__main__":
    unittest.main()
