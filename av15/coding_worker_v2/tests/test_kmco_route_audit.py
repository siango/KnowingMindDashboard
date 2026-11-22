"""All 13 canonical route templates are audited, not spoofed as runnable."""
import unittest
from coding_worker_v2.agent.kmco_route_audit import audit_routes, ROUTES
from coding_worker_v2.agent.tools import build_default_registry
from coding_worker_v2.tests.test_kmco_runtime import catalog


class CanonicalRouteReadinessTests(unittest.TestCase):
    def test_all_13_design_routes_have_honest_runtime_status(self):
        c = catalog()
        reg = build_default_registry(circuits=True, kmco_catalog=c)
        out = audit_routes(c, reg, {"read_only": True})
        self.assertEqual(len(ROUTES), 13)
        self.assertEqual(out["design_route_count"], 13)
        self.assertEqual(out["design_operations"], 132)
        self.assertEqual(out["native_operation_source_bound"], 30)
        self.assertEqual(out["design_only_operations"], 102)
        self.assertEqual(len(out["operation_coverage"]), 132)
        self.assertEqual(len({op["operation_id"] for op in out["operation_coverage"]}), 132)
        for op in out["operation_coverage"]:
            self.assertEqual(op["actual_tools_call"], "NOT_TESTED")
            self.assertEqual(op["independent_qc"], "NOT_RUN")
            if op["group_id"] in ("P01", "P02", "P03", "P04", "P05", "P06"):
                self.assertEqual(op["source_state"],
                                 "NATIVE_HANDLER_REGISTERED_NOT_LIVE_VERIFIED")
            else:
                self.assertEqual(op["source_state"], "DESIGN_ONLY")
        self.assertEqual([row["route_id"] for row in out["routes"]],
                         [f"R{i:02d}" for i in range(1, 14)])
        self.assertEqual(out["verified_end_to_end_routes"], 0)
        self.assertEqual(out["native_problem_circuit_handlers"],
                         {"P01": "kmco.p01.evidence", "P02": "kmco.p02.resume", "P03": "kmco.p03.loop_guard", "P04": "kmco.p04.plan", "P05": "kmco.p05.completion", "P06": "kmco.p06.permission"})
        for route in out["routes"]:
            self.assertTrue(route["unbound_groups"])
            self.assertFalse(route["production_pass"])
            self.assertEqual(route["live_handler_execution"], "NOT_TESTED")
            self.assertEqual(route["source_coverage"], "NO_VERIFIED_ROUTE_BINDINGS")
        self.assertNotIn("P01", out["routes"][0]["design_groups"])
        self.assertIn("K01", out["routes"][0]["design_groups"])

    def test_unbound_catalog_fails_closed(self):
        from coding_worker_v2.agent.kmco_runtime import KMCOError
        with self.assertRaisesRegex(KMCOError, "KMCO_ROUTE_AUDIT_CONTEXT_REQUIRED"):
            audit_routes(None, None, {})


if __name__ == "__main__":
    unittest.main()
