"""Read-only queue/receipt reconciliation tests using temporary JSON stores."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coding_worker_v2.agent import memory, monitor
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.receipts import write_receipt
from coding_worker_v2.tasks import Task


class TestTerminalReconciliation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.store = QueueStore(self.root / "queue")
        self.receipts = self.root / "receipts"
        env = mock.patch.dict(os.environ, {"KMW2_STATE_DIR": str(self.root / "pointer")})
        env.start()
        self.addCleanup(env.stop)

    def task(self):
        return Task.from_dict({"mission_id": "TEST", "task_id": "test-t1",
                              "owner_node": "AV15", "attempts": 1,
                              "input": {"goal": "test goal", "repo": str(self.repo),
                                        "hints": {"base_commit": "c1"}}})

    def conflicting_receipt(self, queue_status="DONE", receipt_status="FAILED"):
        task = self.task()
        task.status = queue_status
        folder = self.store.done if queue_status == "DONE" else self.store.failed
        archive = folder / task.task_id / "task.json"
        self.store._write_json(archive, task.to_dict())
        task.status = receipt_status
        task.evidence.append({"event": "qc_checked", "qc": {"passed": False}})
        receipt_path = write_receipt(self.receipts, task)
        return archive, receipt_path

    def test_conflict_blocks_reuse_and_preserves_both_records(self):
        archive, receipt_path = self.conflicting_receipt()
        before = (archive.read_bytes(), receipt_path.read_bytes())
        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        self.assertEqual((result.decision, result.reason),
                         ("BLOCKED", "QUEUE_RECEIPT_CONFLICT"))
        self.assertEqual(before, (archive.read_bytes(), receipt_path.read_bytes()))

    def test_failed_archive_cannot_be_reused_from_done_receipt(self):
        self.conflicting_receipt("FAILED", "DONE")
        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        self.assertEqual(result.decision, "BLOCKED")

    def test_changed_archive_input_is_reported(self):
        archive, receipt_path = self.conflicting_receipt("FAILED", "FAILED")
        archived = json.loads(archive.read_text())
        archived["input"]["goal"] = "different execution"
        archive.write_text(json.dumps(archived))
        receipt = json.loads(receipt_path.read_text())
        conflicts = memory.terminal_conflicts(self.store, receipt)
        self.assertIn("input", conflicts[0]["fields"])

    def test_unreadable_archive_blocks(self):
        archive, _ = self.conflicting_receipt()
        archive.write_text("invalid json")
        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        self.assertEqual(result.reason, "TERMINAL_RECORD_UNREADABLE")

    def test_invalid_receipt_task_id_is_not_used_as_path(self):
        conflicts = memory.terminal_conflicts(
            self.store, {"task_id": "../../outside"})
        self.assertEqual(conflicts[0]["reason"], "INVALID_RECEIPT_TASK_ID")

    def test_monitor_marks_conflict_blocked_instead_of_done(self):
        self.conflicting_receipt()
        view = monitor.agent_status({"store": self.store,
                                     "receipts_dir": self.receipts,
                                     "progress": {"done": 1, "total": 1},
                                     "qc_status": "DONE"})
        self.assertIsNone(view["PROGRESS_PERCENT"])
        self.assertEqual(view["QC_STATUS"], "BLOCKED")
        self.assertEqual(len(view["reconciliation_conflicts"]), 1)


if __name__ == "__main__":
    unittest.main()
