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
        self.assertEqual(out["native_operation_source_bound"], 78)
        self.assertEqual(out["design_only_operations"], 54)
        self.assertEqual(len(out["operation_coverage"]), 132)
        self.assertEqual(len({op["operation_id"] for op in out["operation_coverage"]}), 132)
        for op in out["operation_coverage"]:
            self.assertEqual(op["actual_tools_call"], "NOT_TESTED")
            self.assertEqual(op["independent_qc"], "NOT_RUN")
            if op["group_id"] in ("P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09", "P10", "P11", "P12", "D01", "D02", "D12"):
                self.assertEqual(op["source_state"],
                                 "NATIVE_HANDLER_REGISTERED_NOT_LIVE_VERIFIED")
            else:
                self.assertEqual(op["source_state"], "DESIGN_ONLY")
        self.assertEqual([row["route_id"] for row in out["routes"]],
                         [f"R{i:02d}" for i in range(1, 14)])
        self.assertEqual(out["verified_end_to_end_routes"], 0)
        self.assertEqual(out["partial_preflight_bindings"], {
            "D06": "kmco.d06.question_preview",
            "D07": "kmco.d07.memory_preview",
            "D08": "kmco.d08.preflight",
            "D09": "kmco.d09.preflight",
            "D10": "kmco.d10.drift_preview",
            "D11": "kmco.d11.observe",
        })
        for op in out["operation_coverage"]:
            if op["group_id"] in out["partial_preflight_bindings"]:
                self.assertEqual(
                    op["partial_preview_handler"],
                    out["partial_preflight_bindings"][op["group_id"]])
                self.assertEqual(op["source_state"], "DESIGN_ONLY")
                self.assertIsNone(op["source_handler"])
            else:
                self.assertIsNone(op["partial_preview_handler"])
        route10 = next(row for row in out["routes"] if row["route_id"] == "R10")
        self.assertEqual(route10["partial_preview_groups"], ["D06", "D07", "D08", "D09"])
        self.assertTrue({"D06", "D07", "D08", "D09"}.issubset(route10["unbound_groups"]))

        self.assertEqual(out["native_problem_circuit_handlers"],
                         {"P01": "kmco.p01.evidence", "P02": "kmco.p02.resume", "P03": "kmco.p03.loop_guard", "P04": "kmco.p04.plan", "P05": "kmco.p05.completion", "P06": "kmco.p06.permission", "P07": "kmco.p07.checkpoint", "P08": "kmco.p08.memory", "P09": "kmco.p09.lease", "P10": "kmco.p10.route", "P11": "kmco.p11.security", "P12": "kmco.p12.providers"})
        for route in out["routes"]:
            self.assertTrue(route["unbound_groups"])
            self.assertFalse(route["production_pass"])
            self.assertEqual(route["live_handler_execution"], "NOT_TESTED")
            self.assertIn(route["source_coverage"], ("NO_VERIFIED_ROUTE_BINDINGS", "PARTIAL_SOURCE_BINDINGS_NOT_E2E"))
        self.assertNotIn("P01", out["routes"][0]["design_groups"])
        self.assertIn("K01", out["routes"][0]["design_groups"])

    def test_unbound_catalog_fails_closed(self):
        from coding_worker_v2.agent.kmco_runtime import KMCOError
        with self.assertRaisesRegex(KMCOError, "KMCO_ROUTE_AUDIT_CONTEXT_REQUIRED"):
            audit_routes(None, None, {})


if __name__ == "__main__":
    unittest.main()
