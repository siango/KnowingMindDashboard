"""V3 memory tests: fingerprints and dedup classification."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import memory  # noqa: E402
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.receipts import write_receipt  # noqa: E402
from coding_worker_v2.tasks import Task  # noqa: E402


def make_task(goal, repo, base="abc123", test_command=""):
    return Task.from_dict({
        "mission_id": "M1", "task_id": "t1", "owner_node": "AV15",
        "input": {"repo": repo, "goal": goal, "test_command": test_command,
                  "hints": {"base_commit": base}}})


class TestFingerprint(unittest.TestCase):
    def test_deterministic(self):
        a = memory.fingerprint_for("Fix It", "/r", "c1")
        b = memory.fingerprint_for("  fix   it ", "/r", "c1")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_repo_and_base_matter(self):
        a = memory.fingerprint_for("same", "/r1", "c1")
        b = memory.fingerprint_for("same", "/r2", "c1")
        c = memory.fingerprint_for("same", "/r1", "c2")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "repo").mkdir()
        self.store = QueueStore(self.root / "q")
        self.receipts = self.root / "receipts"
        self._patch = mock.patch.dict(os.environ, {"KMW2_STATE_DIR": self.tmp.name})
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def test_new(self):
        out = memory.classify("do work", str(self.root / "repo"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "NEW")

    def test_blocked_repo_missing(self):
        out = memory.classify("do work", str(self.root / "nothere"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "BLOCKED")

    def test_monitor_active_lease(self):
        task = make_task("queued work", str(self.root / "repo"))
        self.store.enqueue(task)
        claimed = self.store.claim()
        self.assertIsNotNone(claimed)
        out = memory.classify("queued work", str(self.root / "repo"), "abc123",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "MONITOR")

    def test_resume_expired_lease_checkpoint(self):
        task = make_task("stuck work", str(self.root / "repo"))
        self.store.enqueue(task)
        claimed = self.store.claim()
        assert claimed is not None
        lease = self.store.doing / f"{claimed.task_id}.lease.json"
        data = json.loads(lease.read_text(encoding="utf-8"))
        data["expires_at"] = time.time() - 1
        lease.write_text(json.dumps(data), encoding="utf-8")
        self.store.save_checkpoint(claimed.task_id, "edited", {"changed": ["a.py"]})
        out = memory.classify("stuck work", str(self.root / "repo"), "abc123",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "RESUME")

    def _done_receipt(self, goal, base, qc_pass=True):
        task = make_task(goal, str(self.root / "repo"), base)
        task.status = "DONE"
        task.output = {"changed": ["a.py"]}
        if qc_pass:
            task.evidence.append({"event": "qc_checked",
                                  "qc": {"passed": True}})
        return write_receipt(self.receipts, task)

    def test_reuse_exact_match(self):
        self._done_receipt("reuse me", "c1")
        out = memory.classify("reuse me", str(self.root / "repo"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REUSE")

    def test_reverify_base_changed(self):
        self._done_receipt("shifting", "c1")
        out = memory.classify("shifting", str(self.root / "repo"), "c2",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REVERIFY")
        self.assertIn("BASE_COMMIT_CHANGED", out.reason)

    def test_reverify_stale(self):
        path = self._done_receipt("old news", "c1")
        old = time.time() - 999999
        os.utime(path, (old, old))
        out = memory.classify("old news", str(self.root / "repo"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REVERIFY")

    def test_reverify_no_qc(self):
        self._done_receipt("no qc proof", "c1", qc_pass=False)
        out = memory.classify("no qc proof", str(self.root / "repo"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REVERIFY")

    def test_repair_prior_failure(self):
        task = make_task("flaky", str(self.root / "repo"), "c1")
        task.status = "FAILED"
        task.failure_class = "TEST_FAIL"
        task.attempts = 1
        write_receipt(self.receipts, task)
        out = memory.classify("flaky", str(self.root / "repo"), "c1",
                              self.store, self.receipts, max_attempts=3)
        self.assertEqual(out.decision, "REPAIR")

    def test_reuse_matches_base_workspace_not_worktree(self):
        task = make_task("wt work", "/state/worktrees/x-t1", "c1")
        task.task_input.hints["workspace"] = str(self.root / "repo")
        task.status = "DONE"
        task.output = {"changed": ["a.py"]}
        task.evidence.append({"event": "qc_checked", "qc": {"passed": True}})
        write_receipt(self.receipts, task)
        out = memory.classify("wt work", str(self.root / "repo"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REUSE")

    def test_blocked_retries_exhausted(self):
        task = make_task("hopeless", str(self.root / "repo"), "c1")
        task.status = "FAILED"
        task.failure_class = "TEST_FAIL"
        task.attempts = 5
        write_receipt(self.receipts, task)
        out = memory.classify("hopeless", str(self.root / "repo"), "c1",
                              self.store, self.receipts, max_attempts=3)
        self.assertEqual(out.decision, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
