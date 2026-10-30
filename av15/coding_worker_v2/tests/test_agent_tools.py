"""V3 tool router tests: discovery, validation, permissions, safe exec."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import tools  # noqa: E402
from coding_worker_v2.agent.tools import ToolError, ToolRegistry  # noqa: E402
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.queue_store import QueueStore  # noqa: E402


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "repo").mkdir()
        (self.root / "repo" / "a.py").write_text("print('needle')\n")
        self.reg = tools.build_default_registry()
        self.ctx = {"workspace": str(self.root / "repo"),
                    "store": QueueStore(self.root / "q"),
                    "receipts_dir": str(self.root / "receipts"),
                    "config": WorkerConfig.from_env(),
                    "evidence": []}

    def tearDown(self):
        self.tmp.cleanup()

    def test_discover_never_raises(self):
        rows = self.reg.discover({})
        self.assertTrue(len(rows) >= 10)
        names = {r["tool"] for r in rows}
        for expected in ("repo_inspect", "code_search", "git_diff",
                         "task_enqueue", "git_commit", "pr_create"):
            self.assertIn(expected, names)

    def test_unknown_tool_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("nope_tool", {}, self.ctx)
        self.assertEqual(ctx.exception.reason, "TOOL_UNKNOWN")

    def test_missing_arg_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("code_search", {"repo": "x"}, self.ctx)
        self.assertEqual(ctx.exception.reason, "TOOL_ARG_MISSING")

    def test_unknown_arg_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("repo_inspect", {"repo": "x", "bogus": 1}, self.ctx)
        self.assertEqual(ctx.exception.reason, "TOOL_ARG_UNKNOWN")

    def test_arg_type_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("repo_inspect", {"repo": 123}, self.ctx)
        self.assertEqual(ctx.exception.reason, "TOOL_ARG_TYPE")

    def test_destructive_needs_authorization(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("git_commit", {"repo": "x", "message": "m",
                                         "paths": ["a"]}, self.ctx)
        self.assertEqual(ctx.exception.reason, "PERMISSION_DENIED")

    def test_code_search_finds(self):
        out = self.reg.call("code_search", {"repo": str(self.root / "repo"),
                                            "pattern": "needle"}, self.ctx)
        self.assertTrue(out["matches"])
        self.assertIn("a.py", out["matches"][0]["file"])

    def test_repo_inspect(self):
        out = self.reg.call("repo_inspect", {"repo": str(self.root / "repo")},
                            self.ctx)
        self.assertTrue(out["exists"])

    def test_enqueue_roundtrip(self):
        out = self.reg.call("task_enqueue",
                            {"mission_id": "M1", "task_id": "t1",
                             "repo": str(self.root / "repo"),
                             "goal": "do it"}, self.ctx)
        self.assertTrue(out["created"])
        out2 = self.reg.call("task_enqueue",
                             {"mission_id": "M1", "task_id": "t1",
                              "repo": str(self.root / "repo"),
                              "goal": "do it"}, self.ctx)
        self.assertFalse(out2["created"])

    def test_git_commit_explicit_paths(self):
        repo = str(self.root / "repo")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo,
                       check=True)
        ctx = {**self.ctx, "authorize_destructive": True}
        out = self.reg.call("git_commit", {"repo": repo, "message": "first",
                                           "paths": ["a.py"]}, ctx)
        self.assertEqual(out["rc"], 0)
        log = self.reg.call("git_log", {"repo": repo}, self.ctx)
        self.assertIn("first", " ".join(log["commits"]))

    def test_pr_create_never_shell(self):
        with mock.patch("coding_worker_v2.agent.tools._run",
                        return_value=(0, "https://pr/1")) as run:
            ctx = {**self.ctx, "authorize_destructive": True}
            self.reg.call("pr_create",
                          {"repo": str(self.root / "repo"),
                           "title": "x; rm -rf /"}, ctx)
        argv = run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertIn("x; rm -rf /", argv)  # single argv element, never parsed

    def test_ollama_inventory_honest(self):
        out = self.reg.call("ollama_inventory", {}, self.ctx)
        self.assertIn("available", out)

    def test_evidence_captured(self):
        self.reg.call("repo_inspect", {"repo": str(self.root / "repo")},
                      self.ctx)
        events = [e.get("event") for e in self.ctx["evidence"]]
        self.assertIn("tool_called", events)


if __name__ == "__main__":
    unittest.main()
