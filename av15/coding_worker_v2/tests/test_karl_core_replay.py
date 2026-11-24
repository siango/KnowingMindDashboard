"""KARL only projects existing hash-verified local events through KMCO writer."""
import unittest
from unittest.mock import MagicMock, patch

from coding_worker_v2.agent.kmco_usage import record_karl_event


class KarlCoreReplayTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "event_id": "event-1", "task_id": "task-1",
            "mission_id": "G0-G1-MINIMUM-SELLABLE-DEMO",
            "source_sha": "a" * 40, "status": "PASS",
            "step": "karl.sandbox.observed",
            "detail": {
                "schema": "KMCW_SANDBOX_OBSERVATION_V1",
                "task_id": "task-1", "mission_id": "G0-G1-MINIMUM-SELLABLE-DEMO",
                "source_sha": "a" * 40, "status": "PASS",
                "output_sha256": "b" * 64,
                "failure_class": "NONE",
            },
        }
        self.store = MagicMock()
        self.store.read_events.return_value = {
            "chain_intact": True, "events": [self.event]
        }

    def test_replays_existing_event_without_rerun(self):
        with patch("coding_worker_v2.agent.kmco_usage.record",
                   return_value="PERSISTED_UNVERIFIED") as writer:
            with patch("coding_worker_v2.agent.kmco_core.readback_exact",
                       return_value="PERSISTED_EXACT_READBACK_UNVERIFIED") as reader:
                result = record_karl_event(self.store, "task-1", "event-1")
        self.assertEqual(result, "PERSISTED_EXACT_READBACK_UNVERIFIED")
        reader.assert_called_once()
        writer.assert_called_once()
        args, kwargs = writer.call_args
        self.assertEqual(args[0], "kmco.karl.sandbox")
        self.assertEqual(args[1], {"queue_event_id": "event-1"})
        self.assertTrue(kwargs["idempotent"])
        self.assertEqual(kwargs["result_sha256"], "b" * 64)

    def test_unconfigured_owner_service_does_not_claim_core_write(self):
        with patch("coding_worker_v2.agent.kmco_usage.record",
                   return_value="NOT_CONFIGURED"):
            with patch("coding_worker_v2.agent.kmco_core.readback_exact") as reader:
                result = record_karl_event(self.store, "task-1", "event-1")
                reader.assert_not_called()
        self.assertEqual(result, "CORE_WRITER_NOT_CONFIGURED_LOCAL_EVENT_DURABLE")

    def test_corrupt_chain_blocks_writer(self):
        self.store.read_events.return_value["chain_intact"] = False
        with patch("coding_worker_v2.agent.kmco_usage.record") as writer:
            with self.assertRaisesRegex(RuntimeError, "KARL_EVENT_CHAIN_INVALID"):
                record_karl_event(self.store, "task-1", "event-1")
            writer.assert_not_called()

    def test_cross_task_event_blocks_writer(self):
        self.event["detail"]["task_id"] = "another-task"
        with patch("coding_worker_v2.agent.kmco_usage.record") as writer:
            with self.assertRaisesRegex(RuntimeError, "KARL_OBSERVATION_SCOPE_MISMATCH"):
                record_karl_event(self.store, "task-1", "event-1")
            writer.assert_not_called()

    def test_no_existing_event_blocks_writer(self):
        with patch("coding_worker_v2.agent.kmco_usage.record") as writer:
            with self.assertRaisesRegex(RuntimeError, "KARL_EVENT_IDENTITY_INVALID"):
                record_karl_event(self.store, "task-1", "missing-event")
            writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
