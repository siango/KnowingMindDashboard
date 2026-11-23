"""Checks the evidence wrapper uses the existing bounded execution path."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from coding_worker_v2.agent.sandbox import SandboxError, run_isolated_observed, run_isolated_recorded


SHA = "a" * 40


class SandboxObservationTests(unittest.TestCase):
    def test_allowlisted_success(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "test_ok.py").write_text(
                "import unittest\n"
                "class TestOK(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8")
            result = run_isolated_observed(
                root, "python3 -m unittest discover -s . -p test_ok.py", 15,
                mission_id="G0-G1", task_id="one", source_sha=SHA)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["receipt_status"], "NOT_PERSISTED")
            self.assertNotIn("output", result)

    def test_denies_unallowlisted_command(self):
        with tempfile.TemporaryDirectory() as root:
            result = run_isolated_observed(
                root, "sh -c 'echo forbidden'", 10, mission_id="G0-G1",
                task_id="one", source_sha=SHA)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["failure_class"], "POLICY_DENY")
            self.assertIsNone(result["output_sha256"])

    def test_nonzero_is_failure_not_verified(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("coding_worker_v2.agent.sandbox.run_isolated",
                       return_value={"ran": True, "rc": 2,
                                     "duration_ms": 3, "output": "fail"}):
                result = run_isolated_observed(
                    root, "python3 -m unittest discover", 5,
                    mission_id="G0-G1", task_id="one", source_sha=SHA)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["failure_class"], "TEST_EXIT_NONZERO")
            self.assertEqual(result["independent_qc"], "NOT_RUN")

    def test_recorded_uses_existing_context_and_queue(self):
        with tempfile.TemporaryDirectory() as root:
            store = MagicMock()
            ctx = {"store": store, "workspace": root, "mission_id": "G0-G1",
                   "task_id": "one", "account_id": "posix:1000"}
            with patch("coding_worker_v2.agent.memory.current_base_commit",
                       return_value=SHA):
                with patch("coding_worker_v2.agent.sandbox.run_isolated_observed",
                           return_value={"status": "PASS", "exit_code": 0}):
                    result = run_isolated_recorded(ctx, "python3 -m unittest discover", 10)
            self.assertTrue(result["event_recorded"])
            self.assertEqual(result["core_database_readback"], "NOT_VERIFIED")
            args, kwargs = store.append_event.call_args
            self.assertEqual(args[:3], ("one", "karl.sandbox.observed", "PASS"))
            self.assertEqual(kwargs["mission_id"], "G0-G1")
            self.assertNotIn("task_id", kwargs)

    def test_reject_invalid_sha_without_execution(self):
        with self.assertRaises(SandboxError):
            run_isolated_observed(
                ".", "python3 -m unittest discover", 5,
                mission_id="G0-G1", task_id="one", source_sha="short")


if __name__ == "__main__":
    unittest.main()
