"""KMCO usage recorder contract: no fake DB, no secret or arguments stored."""
import os
import sys
import types
import unittest
from unittest.mock import patch

from coding_worker_v2.agent.kmco_usage import _operation_ids, record
from coding_worker_v2.agent.tools import ToolRegistry, ToolSpec, ToolError


class KMCOUsageTests(unittest.TestCase):
    def test_operation_id_mapping(self):
        self.assertEqual(_operation_ids("kmco.p01.evidence", {}), [
            "P01-M01", "P01-M02", "P01-M03", "P01-M04", "P01-M05"])
        self.assertEqual(_operation_ids("kmco.plan", {"steps": [
            {"op_id": "P04-M01", "after": []},
            {"op_id": "P04-M02", "after": ["P04-M01"]}]}),
            ["P04-M01", "P04-M02"])
        self.assertEqual(_operation_ids("kmco.inspect", {
            "operation_id": "WRONG"}), [])

    def test_default_not_persisted(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(record("kmco.inspect", {"operation_id": "P01-M01"},
                                    {}, ok=True), "NOT_CONFIGURED")
            registry = ToolRegistry()
            registry.register(ToolSpec("kmco.contract_probe", "test"), lambda a,c: {"ok":True})
            ctx = {"workspace": ".", "evidence": []}
            registry.call("kmco.contract_probe", {}, ctx)
            audit = [e for e in ctx["evidence"] if e.get("event") == "kmco_usage_audit"]
            self.assertEqual(audit[-1]["state"], "NOT_CONFIGURED")
            self.assertFalse(audit[-1]["durable"])

    def test_invalid_service_fails_closed(self):
        with patch.dict(os.environ, {"KMCO_PG_SERVICE": "../../bad"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "KMCO_PG_SERVICE_INVALID"):
                record("kmco.inspect", {}, {}, ok=True)

    def test_sql_sink_uses_parameterized_insert_only(self):
        captured = []
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=None):
                captured.append((sql, params))
            def fetchone(self):
                return ("km_microops",)
        class Conn:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def cursor(self): return Cursor()
        fake = types.SimpleNamespace(connect=lambda *a, **kw: Conn())
        with patch.dict(os.environ, {"KMCO_PG_SERVICE": "kmco_runtime"}, clear=True):
            with patch.dict(sys.modules, {"psycopg": fake}):
                state = record("kmco.p01.evidence",
                    {"file":"evidence.txt","quote":"NEVER_STORE_THIS_QUOTE"},
                    {"mission_id":"m","task_id":"t"},ok=True,
                    result_sha256="a"*64)
        self.assertEqual(state, "PERSISTED_UNVERIFIED")
        self.assertEqual(len(captured), 4)
        sql, params = captured[2]
        self.assertIn("INSERT INTO circuit.usage_events", sql)
        self.assertNotIn("NEVER_STORE_THIS_QUOTE", str(params))
        self.assertEqual(params["operation_ids"],
                         [f"P01-M{i:02d}" for i in range(1, 6)])
        self.assertEqual(params["result_sha256"], "a"*64)


if __name__ == "__main__":
    unittest.main()
