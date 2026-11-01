"""V3 executor tests: dedup-aware plan execution over a fake backend."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import executor, planner  # noqa: E402
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
    return planner.simple_plan(command, "KM-TEST", "ptest")


class TestExecutor(FakeBackend):
    def setUp(self):
        FakeState.chat_queue = []
        FakeState.chat_content = FIX

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
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}):
                report = executor.execute_plan(
                    simple_plan_for("fix add() so 2+3==5"), config,
                    QueueStore(root / "q"), root / "receipts", root / "state",
                    repo, use_worktree=False, sleep_fn=lambda s: None)
            self.assertEqual(report["status"], "DONE")
            self.assertEqual(report["tasks"][0]["decision"], "NEW")
            self.assertEqual(report["tasks"][0]["status"], "DONE")
            self.assertTrue((root / "receipts").glob("*.json"))

    def test_reuse_skips_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            qc = {"passed": True, "checks": []}
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}):
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
                          "test_command": "python3 -m unittest"}})
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
