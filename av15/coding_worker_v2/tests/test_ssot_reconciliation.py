"""Read-only queue/receipt reconciliation tests using temporary JSON stores."""
import hashlib
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
        task.lease_token = "reconciliation-test-lease"
        task.status = queue_status
        folder = self.store.done if queue_status == "DONE" else self.store.failed
        archive = folder / task.task_id / "task.json"
        archived = task.to_dict()
        archived["lease_token_sha256"] = hashlib.sha256(
            task.lease_token.encode()).hexdigest()
        self.store._write_json(archive, archived)
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

    def test_corrupt_latest_history_blocks_valid_root_receipt_and_monitor(self):
        task = self.task()
        task.lease_token = "reconciliation-test-lease"
        task.status = "DONE"
        task.output = {"changed": ["calc.py"]}
        task.evidence.append({"event": "qc_checked", "qc": {"passed": True}})
        archive = self.store.done / task.task_id / "task.json"
        archived = task.to_dict()
        archived["lease_token_sha256"] = hashlib.sha256(
            task.lease_token.encode()).hexdigest()
        self.store._write_json(archive, archived)
        root_receipt = write_receipt(self.receipts, task)
        history_receipt = next((self.receipts / "history" / task.task_id).glob("v*.json"))
        history_receipt.write_text("invalid history")

        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        view = monitor.agent_status({"store": self.store,
                                     "receipts_dir": self.receipts,
                                     "progress": {"done": 1, "total": 1},
                                     "qc_status": "DONE"})
        self.assertEqual((result.decision, result.reason),
                         ("BLOCKED", "RECEIPT_HISTORY_UNREADABLE"))
        self.assertIsNone(view["PROGRESS_PERCENT"])
        self.assertEqual(view["QC_STATUS"], "BLOCKED")
        self.assertTrue(root_receipt.is_file())

    def test_failed_receipt_in_done_folder_is_a_location_conflict(self):
        archive, receipt_path = self.conflicting_receipt("FAILED", "FAILED")
        done_archive = self.store.done / "test-t1" / "task.json"
        done_archive.parent.mkdir(parents=True)
        done_archive.write_bytes(archive.read_bytes())
        archive.unlink()
        (self.store.failed / "test-t1").rmdir()

        receipt = json.loads(receipt_path.read_text())
        conflicts = memory.terminal_conflicts(self.store, receipt)
        self.assertTrue(any("terminal_location" in item.get("fields", [])
                            for item in conflicts))

    def test_verifier_signature_evidence_mismatch_blocks_reuse(self):
        task = self.task()
        task.lease_token = "reconciliation-test-lease"
        task.status = "DONE"
        task.output = {"changed": ["calc.py"], "patch_digest": "a" * 64}
        task.evidence.extend([
            {"event": "qc_checked", "qc": {"passed": True}},
            {"event": "independent_verification",
             "proof": {"signature_sha256": "b" * 64}},
        ])
        archive = self.store.done / task.task_id / "task.json"
        archived = task.to_dict()
        archived["lease_token_sha256"] = hashlib.sha256(
            task.lease_token.encode()).hexdigest()
        self.store._write_json(archive, archived)
        receipt_path = write_receipt(self.receipts, task)
        archived = json.loads(archive.read_text())
        archived["evidence"][-1]["proof"]["signature_sha256"] = "c" * 64
        archive.write_text(json.dumps(archived))

        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        self.assertEqual((result.decision, result.reason),
                         ("BLOCKED", "QUEUE_RECEIPT_CONFLICT"))
        self.assertTrue(receipt_path.is_file())

    def test_terminal_archive_records_only_the_claimed_lease_digest(self):
        task = self.task()
        self.store.enqueue(task)
        claimed = self.store.claim()
        self.assertIsNotNone(claimed)
        claimed.status = "DONE"
        claimed.output = {"changed": ["calc.py"]}
        claimed.evidence.append({"event": "qc_checked", "qc": {"passed": True}})
        receipt_path = write_receipt(self.receipts, claimed)
        receipt = json.loads(receipt_path.read_text())
        self.store.complete(claimed)
        archive = json.loads((self.store.done / claimed.task_id / "task.json").read_text())
        self.assertEqual(archive["lease_token_sha256"],
                         receipt["lease_token_sha256"])
        self.assertNotIn("lease_token", archive)

    def test_terminal_archive_without_receipt_blocks_duplicate_intake(self):
        task = self.task()
        task.status = "DONE"
        archive = self.store.done / task.task_id / "task.json"
        self.store._write_json(archive, task.to_dict())
        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        view = monitor.agent_status({"store": self.store,
                                     "receipts_dir": self.receipts,
                                     "progress": {"done": 1, "total": 1},
                                     "qc_status": "DONE"})
        self.assertEqual((result.decision, result.reason),
                         ("BLOCKED", "TERMINAL_RECEIPT_MISSING"))
        self.assertIsNone(view["PROGRESS_PERCENT"])
        self.assertEqual(view["QC_STATUS"], "BLOCKED")

    def test_qc_pending_receipt_blocks_automatic_repair(self):
        task = self.task()
        task.status = "QC_PENDING"
        task.attempts = 1
        write_receipt(self.receipts, task)
        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        self.assertEqual((result.decision, result.reason),
                         ("BLOCKED", "AWAIT_INDEPENDENT_VERIFICATION"))


if __name__ == "__main__":
    unittest.main()
