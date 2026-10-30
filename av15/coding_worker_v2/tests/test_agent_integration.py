"""V3 live integration: real Ollama gates. Skipped without KMW2_LIVE=1.

CI-safe: every test skips honestly when the live backend is absent.
Production acceptance runs these with KMW2_LIVE=1 on AV15 (host net).
"""
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
from coding_worker_v2.providers import (ProviderError,  # noqa: E402
                                        generate_with_fallback)
from coding_worker_v2.queue_store import QueueStore  # noqa: E402


def live_config():
    return WorkerConfig.from_env()


def require_live(testcase):
    if os.environ.get("KMW2_LIVE", "") != "1":
        testcase.skipTest("KMW2_LIVE != 1")
    chain = executor.qwen_chain(live_config())
    if not chain:
        testcase.skipTest("live Ollama unavailable")
    return chain


class TestLivePlanner(unittest.TestCase):
    def test_live_plan_validates(self):
        chain = require_live(self)
        config = live_config()
        gen, _ = generate_with_fallback(
            planner.plan_messages("fix add(a,b) so it returns a+b",
                                  {"files": 3, "git_branch": "main",
                                   "git_clean": True},
                                  ["repo_inspect", "code_search"]),
            chain, max_tokens=1024, timeout=config.task_timeout_s)
        data = planner.parse_plan(gen.text)
        data.setdefault("mission_id", "KM-LIVE")
        data.setdefault("plan_id", "plive")
        data.setdefault("owner_node", "AV15")
        plan = planner.validate_plan_dict(
            data, ["repo_inspect", "code_search"])
        self.assertTrue(plan.tasks)
        self.assertEqual(gen.provider, "qwen-local")


class TestLiveRun(unittest.TestCase):
    def test_live_coding_task_passes(self):
        chain = require_live(self)
        _ = chain
        config = live_config()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
            (repo / "test_calc.py").write_text(
                "import unittest\nfrom calc import add\n\n"
                "class T(unittest.TestCase):\n def test_add(self):\n"
                "  self.assertEqual(add(2, 3), 5)\n")
            plan = planner.simple_plan("fix add() so 2+3==5", "KM-LIVE",
                                       "plive1")
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}):
                report = executor.execute_plan(
                    plan, config, QueueStore(root / "q"), root / "receipts",
                    root / "state", str(repo), use_worktree=False,
                    sleep_fn=lambda s: None)
            self.assertEqual(report["status"], "DONE", json.dumps(report)[:800])
            receipt = json.loads(
                (root / "receipts" / "plive1-t1.json").read_text(
                    encoding="utf-8"))
            gens = [e for e in receipt["evidence"]
                    if e.get("event") == "model_generated"]
            self.assertTrue(gens)
            self.assertEqual(gens[0]["provider"], "qwen-local")


if __name__ == "__main__":
    unittest.main()
