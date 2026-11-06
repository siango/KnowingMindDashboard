"""V3.1 worktree + sandbox tests: isolation, retention, confinement."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import sandbox  # noqa: E402
from coding_worker_v2.agent.sandbox import SandboxError  # noqa: E402
from coding_worker_v2.agent.worktree import (WorktreeError,  # noqa: E402
                                             WorktreeRegistry)


def init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path,
                   check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "f.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)
    return str(path)


class TestWorktree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = init_repo(self.root / "repo")
        self.reg = WorktreeRegistry(self.root / "state")

    def tearDown(self):
        for entry in self.reg.list():
            if entry.get("status") == "active":
                try:
                    self.reg.cleanup(entry["task_id"], allow_dirty=True)
                except WorktreeError:
                    pass
        self.tmp.cleanup()

    def test_create_and_status(self):
        entry = self.reg.create(self.repo, "task1")
        self.assertEqual(entry["branch"], "km/task1")
        self.assertTrue(Path(entry["path"]).is_dir())
        status = self.reg.status("task1")
        self.assertFalse(status["dirty"])

    def test_create_idempotent(self):
        first = self.reg.create(self.repo, "task1")
        second = self.reg.create(self.repo, "task1")
        self.assertEqual(first["path"], second["path"])

    def test_registered_original_workspace_is_not_treated_as_isolated(self):
        self.reg._write({"task1": {"task_id": "task1", "status": "active",
                                    "path": self.repo, "repo": self.repo}})
        with self.assertRaises(WorktreeError) as raised:
            self.reg.create(self.repo, "task1")
        self.assertEqual(raised.exception.reason, "OWNERSHIP_REFUSED")

    def test_branch_isolated_from_base(self):
        entry = self.reg.create(self.repo, "task1")
        (Path(entry["path"]) / "new.txt").write_text("work\n")
        self.assertFalse((Path(self.repo) / "new.txt").exists())

    def test_dirty_cleanup_preserves_tracked_and_untracked_files(self):
        entry = self.reg.create(self.repo, "task1")
        worktree = Path(entry["path"])
        (worktree / "f.txt").write_text("tracked-change\n")
        (worktree / "new.txt").write_text("untracked-content\n")
        with self.assertRaises(WorktreeError) as ctx:
            self.reg.cleanup("task1")
        self.assertEqual(ctx.exception.reason, "DIRTY_REFUSED")
        with self.assertRaises(WorktreeError) as ctx:
            self.reg.cleanup("task1", allow_dirty=True)
        self.assertEqual(ctx.exception.reason, "DIRTY_REFUSED")
        self.assertTrue(worktree.is_dir())
        self.assertEqual((worktree / "f.txt").read_text(), "tracked-change\n")
        self.assertEqual((worktree / "new.txt").read_text(), "untracked-content\n")

    def test_clean_cleanup(self):
        self.reg.create(self.repo, "task1")
        out = self.reg.cleanup("task1")
        self.assertTrue(out["removed"])
        self.assertEqual(out["retained_patch"], "")

    def test_recover_orphaned(self):
        entry = self.reg.create(self.repo, "task1")
        subprocess.run(["git", "-C", self.repo, "worktree", "remove", "--force",
                        entry["path"]], check=True)
        result = self.reg.recover()
        self.assertIn("task1", result["orphaned"])

    def test_restart_recovery_preserves_dirty_worktree_contents(self):
        entry = self.reg.create(self.repo, "task1")
        path = Path(entry["path"])
        (path / "f.txt").write_text("tracked-after-crash\n")
        (path / "recovered.txt").write_text("untracked-after-crash\n")
        restarted = WorktreeRegistry(self.root / "state")
        result = restarted.recover()
        self.assertIn("task1", result["active"])
        self.assertEqual((path / "f.txt").read_text(),
                         "tracked-after-crash\n")
        self.assertEqual((path / "recovered.txt").read_text(),
                         "untracked-after-crash\n")

    def test_unknown_task(self):
        with self.assertRaises(WorktreeError):
            self.reg.status("ghost")


class TestSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "repo").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_confinement_ok(self):
        path = sandbox.check_confinement(str(self.root / "repo"), "a/b.txt")
        self.assertTrue(str(path).startswith(str(self.root)))

    def test_confinement_escape(self):
        with self.assertRaises(SandboxError) as ctx:
            sandbox.check_confinement(str(self.root / "repo"), "../escape.txt")
        self.assertEqual(ctx.exception.reason, "ESCAPE")

    def test_scrubbed_env(self):
        env = dict(os.environ)
        env["KMW2_TEST_SECRET_TOKEN"] = "s3cret"
        with mock.patch.dict(os.environ, env, clear=True):
            clean = sandbox.scrubbed_env()
        self.assertNotIn("KMW2_TEST_SECRET_TOKEN", clean)

    def test_allowlist_enforced(self):
        with self.assertRaises(SandboxError) as ctx:
            sandbox.run_isolated(str(self.root / "repo"), "rm -rf /tmp/x", 5)
        self.assertEqual(ctx.exception.reason, "POLICY_DENY")

    def test_policy_auto_approves_only_scoped_safe_actions(self):
        workspace = str(self.root / "repo")
        for action in ("inspect", "git_diff", "checkpoint", "receipt", "idempotent_retry"):
            with self.subTest(action=action):
                event = sandbox.policy_decision(action, "task", workspace, paths=["a.py"])
                self.assertEqual(event["decision"], "AUTO_APPROVE")
                self.assertTrue(event["rule_id"])
                self.assertEqual(event["task_id"], "task")
                self.assertEqual(event["event"], "approval_decision")
        self.assertEqual(sandbox.policy_decision(
            "edit", "task", workspace, paths=["a.py"], approved_paths=["a.py"],
            isolated=True)["decision"], "AUTO_APPROVE")
        self.assertEqual(sandbox.policy_decision(
            "test", "task", workspace, command="python3 -m unittest")["decision"],
            "AUTO_APPROVE")

    def test_policy_human_gates_unsafe_operations_and_command_prefix_tricks(self):
        workspace = str(self.root / "repo")
        for action, paths, command in (
                ("deploy", [], ""), ("merge", [], ""), ("force_push", [], ""),
                ("independent_qc_sign", [], ""), ("delete", ["a.py"], ""),
                ("edit", ["a.py"], ""), ("inspect", ["../outside"], ""),
                ("edit", [".env"], ""), ("inspect", ["id_rsa"], ""),
                ("edit", ["wrangler.toml"], ""), ("edit", [".github/workflows/ci.yml"], ""),
                ("test", [], "python3 -m unittest_evil"),
                ("test", [], "node arbitrary-script.js"),
                ("test", [], "python3 -m unittest; echo yes"),
                ("lint", [], "ruff check --fix ."),
                ("test", [], "python3 -m unittest ../outside.py")):
            with self.subTest(action=action, paths=paths, command=command):
                event = sandbox.policy_decision(
                    action, "task", workspace, paths=paths,
                    approved_paths=paths, isolated=True, command=command)
                if action == "edit" and paths == ["a.py"]:
                    event = sandbox.policy_decision(
                        action, "task", workspace, paths=paths,
                        approved_paths=paths, isolated=False)
                self.assertEqual(event["decision"], "REQUIRE_HUMAN_APPROVAL")

    def test_verify_passes(self):
        report = sandbox.verify(str(self.root / "repo"))
        self.assertTrue(report["passed"])
        self.assertEqual(report["schema"], "KM_SANDBOX_REPORT_V1")

    def test_verify_missing_repo(self):
        report = sandbox.verify(str(self.root / "nothere"))
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
