"""End-to-end: fake Qwen/Ollama backend drives the full task loop.

Proves the Qwen-local coding path (route -> edit -> syntax -> test ->
receipt) works end to end given a reachable Ollama; the only gap to a
live G2 is the missing Ollama service on this host.
"""
import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.agent.auditor import SCOPE_DIGEST  # noqa: E402
from coding_worker_v2.providers import Generation  # noqa: E402
from coding_worker_v2.receipts import read_receipt, write_receipt  # noqa: E402
from coding_worker_v2 import run_once  # noqa: E402
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
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                   check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo,
                   check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    return str(repo)


def direct_scope(task_id: str, mission_id: str, goal: str,
                 allowed_paths: list[str]) -> dict:
    plan_id = f"direct-{task_id}"
    acceptance = ["task goal implemented and tests pass"]
    return {
        "plan_id": plan_id,
        "plan_digest": "0" * 64,
        "scope_digest": SCOPE_DIGEST,
        "base_commit": "",
        "acceptance": acceptance,
        "approved_scope": {
            "mission_id": mission_id,
            "plan_id": plan_id,
            "revision": 1,
            "owner_node": "AV15",
            "queue_task_id": task_id,
            "source_command": goal,
            "plan_digest": "0" * 64,
            "scope_digest": SCOPE_DIGEST,
            "task_id": task_id,
            "goal": goal,
            "acceptance": acceptance,
            "base_commit": "",
            "allowed_paths": allowed_paths,
        },
    }


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

    def test_legacy_once_is_blocked_before_queue_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                status = run_once.main(["--once", "--base", tmp])
            self.assertEqual(status, 2)
            report = json.loads(output.getvalue())
            self.assertEqual(report["error"], "LEGACY_EXECUTION_BLOCKED")
            health = QueueStore(Path(tmp)).health()
            self.assertEqual(health["inbox"], 0)
            self.assertEqual(health["doing"], 0)

    def test_qwen_e2e_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = fixture_repo(Path(tmp))
            config = config_for(self.port)
            store = QueueStore(Path(tmp) / "q")
            task = Task.from_dict({
                "mission_id": "KM-E2E", "task_id": "e2e1", "owner_node": "AV15",
                "input": {"repo": repo, "goal": "fix add() so 2+3==5",
                          "test_command": "python3 -m unittest",
                          "preferred_provider": "qwen-local",
                          "hints": direct_scope(
                              "e2e1", "KM-E2E", "fix add() so 2+3==5",
                              ["calc.py"])}})
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

    def test_heartbeat_keeps_lease_during_slow_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = fixture_repo(root)
            config = config_for(self.port)
            store = QueueStore(root / "q")
            task = Task.from_dict({
                "mission_id": "KM-E2E", "task_id": "slow-inference",
                "owner_node": "AV15",
                "input": {"repo": repo, "goal": "fix add()",
                          "test_command": "python3 -m unittest",
                          "preferred_provider": "qwen-local",
                          "hints": direct_scope(
                              "slow-inference", "KM-E2E", "fix add()",
                              ["calc.py"])}})
            store.enqueue(task)
            claimed = store.claim()
            assert claimed is not None

            def slow_generation(*_args, **_kwargs):
                time.sleep(0.15)
                return Generation(text=FIX, provider="qwen-local",
                                  model="qwen-test"), []

            with mock.patch("coding_worker_v2.queue_store.LEASE_TTL_S", 0.06), \
                    mock.patch("coding_worker_v2.queue_store.HEARTBEAT_EVERY_S",
                               0.01), \
                    mock.patch("coding_worker_v2.run_once.generate_with_fallback",
                               side_effect=slow_generation), \
                    mock.patch.object(store, "heartbeat",
                                      wraps=store.heartbeat) as heartbeat:
                result = run_task(claimed, store, config,
                                  Path(tmp) / "spend.json",
                                  chain=[SimpleNamespace(name="qwen-local")])
            self.assertEqual(result.status, "DONE")
            self.assertGreaterEqual(heartbeat.call_count, 3)
            self.assertTrue(store.lease_valid(result.task_id, result.lease_token))
            store.complete(result)

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
                          "preferred_provider": "qwen-local",
                          "hints": direct_scope(
                              "e2e2", "KM-E2E", "fix add()",
                              ["calc.py"])}})
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
                              "test_command": "", "max_repairs": 0,
                              "hints": direct_scope(
                                  "e2e4", "KM-E2E", "echo cannot author edits",
                                  ["calc.py"])}})
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
                    "input": {"repo": repo, "goal": "fix add()",
                              "hints": direct_scope(
                                  "e2e3", "KM-E2E", "fix add()",
                                  ["calc.py"])}})
                store.enqueue(task)
                claimed = store.claim()
                assert claimed is not None
                result = run_task(claimed, store, config, Path(tmp) / "spend.json")
                self.assertEqual(result.status, "FAILED")
                self.assertEqual(result.failure_class, "PROVIDER_FAIL")

    def test_missing_approved_scope_prevents_any_model_call_or_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = fixture_repo(Path(tmp))
            config = config_for(self.port)
            store = QueueStore(Path(tmp) / "q")
            task = Task.from_dict({
                "mission_id": "KM-E2E", "task_id": "no-scope",
                "owner_node": "AV15",
                "input": {"repo": repo, "goal": "fix add()"}})
            store.enqueue(task)
            claimed = store.claim()
            assert claimed is not None
            with mock.patch("coding_worker_v2.run_once.generate_with_fallback") as gen:
                result = run_task(claimed, store, config,
                                  Path(tmp) / "spend.json")
            self.assertEqual(result.status, "FAILED")
            self.assertEqual(result.failure_class, "POLICY_DENY")
            self.assertEqual(result.next_action, "APPROVED_SCOPE_MISSING")
            self.assertFalse(gen.called)
            source = (Path(repo) / "calc.py").read_text()
            self.assertIn("return a - b", source)


if __name__ == "__main__":
    unittest.main()
