"""Source-only KMCO KEBUNATE core tests, no fake production verification."""
import json
import os
import unittest
from unittest.mock import patch
from coding_worker_v2.agent.kmco_core import _scope, observe, recall


class CoreMemoryTests(unittest.TestCase):
    def test_requires_exact_mission_task(self):
        with self.assertRaisesRegex(RuntimeError, "KMCO_CORE_SCOPE_REQUIRED"):
            _scope({"mission_id": "m"})
        self.assertEqual(_scope({"mission_id": "m", "task_id": "t"}), ("m", "t"))

    def test_observed_failure_never_invents_cause_or_qc(self):
        class Cursor:
            sql = None
            args = None
            def execute(self, sql, args):
                self.sql, self.args = sql, args
        cur = Cursor()
        observe(cur, "kmco.p01.evidence", "a" * 64, ["P01-M01"],
                {"mission_id": "m", "task_id": "t"}, ok=False,
                error_class="KMCO_P01_DENIED")
        self.assertIn("ON CONFLICT", cur.sql)
        self.assertEqual(cur.args["state"], "BLOCKED_UNVERIFIED")
        self.assertEqual(json.loads(cur.args["dukkha"])["observed"], "KMCO_P01_DENIED")
        self.assertIsNone(json.loads(cur.args["samudaya"])["cause"])
        self.assertFalse(json.loads(cur.args["nirodha"])["verified"])
        self.assertFalse(json.loads(cur.args["magga"])["executed"])

    def test_success_not_independent_qc(self):
        class Cursor:
            def execute(self, sql, args): self.args = args
        cur = Cursor()
        observe(cur, "kmco.p01.evidence", "b" * 64, ["P01-M01"],
                {"mission_id": "m", "task_id": "t"}, ok=True,
                result_sha256="c" * 64)
        self.assertEqual(cur.args["state"], "EXECUTED_UNVERIFIED")
        self.assertFalse(json.loads(cur.args["nirodha"])["verified"])

    def test_recall_fails_closed_without_db_service(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "NOT_CONFIGURED"):
                recall({"mission_id": "m", "task_id": "t"})


if __name__ == "__main__":
    unittest.main()
