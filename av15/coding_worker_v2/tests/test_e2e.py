"""End-to-end: fake Qwen/Ollama backend drives the full task loop.

Proves the Qwen-local coding path (route -> edit -> syntax -> test ->
receipt) works end to end given a reachable Ollama; the only gap to a
live G2 is the missing Ollama service on this host.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.receipts import read_receipt, write_receipt  # noqa: E402
from coding_worker_v2.run_once import run_task  # noqa: E402
from coding_worker_v2.tasks import Task  # noqa: E402
from coding_worker_v2.tests import FakeBackend, FakeState  # noqa: E402

FIX = json.dumps({"edits": [{"path": "calc.py", "old": "return a - b",
                              "new": "return a + b"}]})


def fixture_repo(root: Path) -> str:
    repo = root / "repo"
    repo.mkdir()
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
        for key in ("KMW2_QWEN_MODEL", "KMW2_DEEPSEEK_API_KEY"):
            os.environ.pop(key, None)
        return WorkerConfig.from_env()


class TestE2E(FakeBackend):
    def setUp(self):
        FakeState.chat_queue = []
        FakeState.chat_content = FIX
        self._tmpstate = tempfile.TemporaryDirectory()
        self._state_patch = mock.patch.dict(
            os.environ, {"KMW2_STATE_DIR": self._tmpstate.name})
        self._state_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        self._tmpstate.cleanup()

    def test_qwen_e2e_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = fixture_repo(Path(tmp))
            config = config_for(self.port)
            store = QueueStore(Path(tmp) / "q")
            task = Task.from_dict({
                "mission_id": "KM-E2E", "task_id": "e2e1", "owner_node": "AV15",
                "input": {"repo": repo, "goal": "fix add() so 2+3==5",
                          "test_command": "python3 -m unittest",
                          "preferred_provider": "qwen-local"}})
            store.enqueue(task)
            claimed = store.claim()
            assert claimed is not None
            result = run_task(claimed, store, config, Path(tmp) / "spend.json")
            self.assertEqual(result.status, "DONE")
            self.assertEqual(result.output["provider"], "qwen-local")
            self.assertEqual(result.output["changed"], ["calc.py"])
            store.complete(result)
            receipt = write_receipt(Path(tmp) / "receipts", result)
            back = read_receipt(receipt)
            self.assertEqual(back["status"], "DONE")
            events = [e.get("event") for e in back["evidence"]]
            for expected in ("repo_inspected", "route_selected", "model_generated",
                             "syntax_checked", "tests_run"):
                self.assertIn(expected, events)

    def test_repair_after_invalid_output(self):
        FakeState.chat_queue = ["oops not json", FIX]
        with tempfile.TemporaryDirectory() as tmp:
            repo = fixture_repo(Path(tmp))
            config = config_for(self.port)
            store = QueueStore(Path(tmp) / "q")
            task = Task.from_dict({
                "mission_id": "KM-E2E", "task_id": "e2e2", "owner_node": "AV15",
                "input": {"repo": repo, "goal": "fix add()",
                          "test_command": "python3 -m unittest",
                          "preferred_provider": "qwen-local"}})
            store.enqueue(task)
            claimed = store.claim()
            assert claimed is not None
            result = run_task(claimed, store, config, Path(tmp) / "spend.json")
            self.assertEqual(result.status, "DONE")
            self.assertEqual(result.output["repairs_used"], 1)

    def test_parse_failure_class_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = fixture_repo(Path(tmp))
            with mock.patch.dict(os.environ, {
                    "KMW2_OLLAMA_URL": "http://127.0.0.1:9",
                    "KMW2_ALLOW_ECHO_FALLBACK": "1"}, clear=False):
                config = WorkerConfig.from_env()
            with mock.patch("coding_worker_v2.run_once.route_chain") as chained:
                from coding_worker_v2.providers import build_providers
                chained.return_value = [build_providers(config)["echo"]]
                store = QueueStore(Path(tmp) / "q")
                task = Task.from_dict({
                    "mission_id": "KM-E2E", "task_id": "e2e4", "owner_node": "AV15",
                    "input": {"repo": repo, "goal": "echo cannot author edits",
                              "test_command": "", "max_repairs": 0}})
                store.enqueue(task)
                claimed = store.claim()
                assert claimed is not None
                result = run_task(claimed, store, config, Path(tmp) / "spend.json")
                self.assertEqual(result.status, "FAILED")
                self.assertEqual(result.failure_class, "PARSE_FAIL")

    def test_no_provider_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = fixture_repo(Path(tmp))
            with mock.patch.dict(os.environ, {
                    "KMW2_OLLAMA_URL": "http://127.0.0.1:9",
                    "KMW2_ALLOW_ECHO_FALLBACK": "0"}, clear=False):
                config = WorkerConfig.from_env()
            with mock.patch("coding_worker_v2.run_once.route_chain", return_value=[]):
                store = QueueStore(Path(tmp) / "q")
                task = Task.from_dict({
                    "mission_id": "KM-E2E", "task_id": "e2e3", "owner_node": "AV15",
                    "input": {"repo": repo, "goal": "fix add()"}})
                store.enqueue(task)
                claimed = store.claim()
                assert claimed is not None
                result = run_task(claimed, store, config, Path(tmp) / "spend.json")
                self.assertEqual(result.status, "FAILED")
                self.assertEqual(result.failure_class, "PROVIDER_FAIL")


if __name__ == "__main__":
    unittest.main()
