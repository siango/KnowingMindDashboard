"""Checks the evidence wrapper uses the existing bounded execution path."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_worker_v2.agent.sandbox import SandboxError, run_isolated_observed


SHA = "a" * 40


class SandboxObservationTests(unittest.TestCase):
    def test_allowlisted_success(self):
        with tempfile.TemporaryDirectory() as root:
            result = run_isolated_observed(
                root, "python3 -m unittest discover -s . -p test_no_such_file.py", 15,
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

    def test_reject_invalid_sha_without_execution(self):
        with self.assertRaises(SandboxError):
            run_isolated_observed(
                ".", "python3 -m unittest discover", 5,
                mission_id="G0-G1", task_id="one", source_sha="short")


if __name__ == "__main__":
    unittest.main()
