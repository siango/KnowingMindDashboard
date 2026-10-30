"""V3.1 worktree + sandbox tests: isolation, retention, confinement."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_branch_isolated_from_base(self):
        entry = self.reg.create(self.repo, "task1")
        (Path(entry["path"]) / "new.txt").write_text("work\n")
        self.assertFalse((Path(self.repo) / "new.txt").exists())

    def test_dirty_refused_then_retained(self):
        entry = self.reg.create(self.repo, "task1")
        (Path(entry["path"]) / "f.txt").write_text("dirty\n")
        with self.assertRaises(WorktreeError) as ctx:
            self.reg.cleanup("task1")
        self.assertEqual(ctx.exception.reason, "DIRTY_REFUSED")
        out = self.reg.cleanup("task1", allow_dirty=True)
        self.assertTrue(out["removed"])
        self.assertTrue(Path(out["retained_patch"]).is_file())

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
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            clean = sandbox.scrubbed_env()
        self.assertNotIn("KMW2_TEST_SECRET_TOKEN", clean)

    def test_allowlist_enforced(self):
        with self.assertRaises(SandboxError) as ctx:
            sandbox.run_isolated(str(self.root / "repo"), "rm -rf /tmp/x", 5)
        self.assertEqual(ctx.exception.reason, "POLICY_DENY")

    def test_verify_passes(self):
        report = sandbox.verify(str(self.root / "repo"))
        self.assertTrue(report["passed"])
        self.assertEqual(report["schema"], "KM_SANDBOX_REPORT_V1")

    def test_verify_missing_repo(self):
        report = sandbox.verify(str(self.root / "nothere"))
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
