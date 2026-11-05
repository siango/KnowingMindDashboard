"""Read-only reconciliation and QC-before-terminal-archive regression tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coding_worker_v2.agent import executor, memory, monitor, planner
from coding_worker_v2.config import WorkerConfig
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

    def conflict(self, queue_status="DONE", receipt_status="FAILED"):
        task = self.task()
        task.status = queue_status
        folder = self.store.done if queue_status == "DONE" else self.store.failed
        path = folder / task.task_id / "task.json"
        self.store._write_json(path, task.to_dict())
        task.status = receipt_status
        task.evidence.append({"event": "qc_checked", "qc": {"passed": False}})
        receipt = write_receipt(self.receipts, task)
        return path, receipt

    def test_conflict_blocks_without_rewriting_either_record(self):
        paths = self.conflict()
        before = {p: p.read_bytes() for p in paths}
        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        self.assertEqual(result.decision, "BLOCKED")
        self.assertEqual(result.reason, "QUEUE_RECEIPT_CONFLICT")
        self.assertEqual(before, {p: p.read_bytes() for p in paths})

    def test_failed_archive_cannot_be_reused_from_done_receipt(self):
        self.conflict("FAILED", "DONE")
        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        self.assertEqual(result.decision, "BLOCKED")

    def test_changed_archive_input_is_reported(self):
        path, receipt_path = self.conflict("FAILED", "FAILED")
        archived = json.loads(path.read_text())
        archived["input"]["goal"] = "different execution"
        path.write_text(json.dumps(archived))
        conflicts = memory.terminal_conflicts(self.store,
                                              json.loads(receipt_path.read_text()))
        self.assertIn("input", conflicts[0]["fields"])

    def test_unreadable_archive_blocks(self):
        path, _ = self.conflict()
        path.write_text("invalid json")
        result = memory.classify("test goal", str(self.repo), "c1",
                                 self.store, self.receipts)
        self.assertEqual(result.reason, "TERMINAL_RECORD_UNREADABLE")

    def test_invalid_id_does_not_follow_path(self):
        result = memory.terminal_conflicts(self.store, {"task_id": "../../outside"})
        self.assertEqual(result[0]["reason"], "INVALID_RECEIPT_TASK_ID")

    def test_monitor_overrides_done_progress_on_conflict(self):
        self.conflict()
        view = monitor.agent_status({"store": self.store,
                                     "receipts_dir": self.receipts,
                                     "progress": {"done": 1, "total": 1},
                                     "qc_status": "DONE"})
        self.assertIsNone(view["PROGRESS_PERCENT"])
        self.assertEqual(view["QC_STATUS"], "BLOCKED")
        self.assertEqual(len(view["reconciliation_conflicts"]), 1)

    def execute(self, qc_fn):
        def done(task, *args, **kwargs):
            task.status = "DONE"
            task.next_action = "REVIEW_PATCH"
            return task
        with mock.patch.object(executor, "current_base_commit", return_value="c1"), \
             mock.patch.object(executor, "qwen_chain", return_value=[]), \
             mock.patch.object(executor, "run_task", side_effect=done):
            return executor.execute_plan(
                planner.simple_plan("test goal", "TEST", "test"), WorkerConfig(),
                self.store, self.receipts, self.root / "state", str(self.repo),
                use_worktree=False, qc_fn=qc_fn, sleep_fn=lambda _: None)

    def test_qc_failure_archives_failed_without_requeue(self):
        def qc(task, ctx):
            self.assertFalse((self.store.done / task.task_id / "task.json").exists())
            self.assertTrue((self.store.doing / (task.task_id + ".json")).exists())
            return {"passed": False}
        report = self.execute(qc)
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(self.store.health(), {"inbox": 0, "doing": 0,
                                             "done": 0, "failed": 1})
        receipt = json.loads(next(self.receipts.glob("*.json")).read_text())
        archived = json.loads(next(self.store.failed.glob("*/task.json")).read_text())
        self.assertEqual(receipt["status"], "FAILED")
        self.assertEqual(archived["status"], "FAILED")
        self.assertEqual(archived["failure_class"], "TEST_FAIL")
        self.assertFalse(archived.get("evidence", [])[-1]["qc"]["passed"])

    def test_qc_success_queue_and_receipt_agree(self):
        self.execute(lambda task, ctx: {"passed": True})
        receipt = json.loads(next(self.receipts.glob("*.json")).read_text())
        archived = json.loads(next(self.store.done.glob("*/task.json")).read_text())
        self.assertEqual(receipt["status"], "DONE")
        self.assertEqual(memory.terminal_conflicts(self.store, receipt), [])
        self.assertTrue(archived["evidence"][-1]["qc"]["passed"])

    def test_receipt_failure_does_not_create_done_archive(self):
        with mock.patch.object(executor, "write_receipt", side_effect=OSError("TEST")):
            with self.assertRaises(OSError):
                self.execute(lambda task, ctx: {"passed": True})
        self.assertEqual(self.store.health()["done"], 0)
        self.assertEqual(self.store.health()["doing"], 1)

    def test_conflict_blocks_execution_before_qc(self):
        self.conflict()
        report = self.execute(lambda task, ctx: self.fail("QC must not execute"))
        self.assertEqual(report["tasks"][0]["decision"], "BLOCKED")
        self.assertEqual(report["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
