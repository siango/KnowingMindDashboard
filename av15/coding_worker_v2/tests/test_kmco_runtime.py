"""KMCO contract and existing ToolRegistry integration: fail closed."""
import hashlib
import unittest

from av15.coding_worker_v2.agent.kmco_runtime import KMCOCatalog, KMCOError, validate_route, register_kmco_tools
from av15.coding_worker_v2.agent.tools import ToolRegistry, ToolError


def catalog():
    rows = []
    hashes = {"core.md": "a" * 64, "domain.md": "b" * 64}
    for prefix, n, file in (("P", 5, "core.md"), ("D", 6, "domain.md")):
        for i in range(1, 13):
            for j in range(1, n + 1):
                op_id = f"{prefix}{i:02d}-M{j:02d}"
                rows.append({"operation_id": op_id,
                             "group_id": op_id.split("-")[0],
                             "source_file": file,
                             "source_sha256": hashes[file],
                             "source_cells": [op_id, "design only"]})
    return KMCOCatalog(rows, source_digests=hashes)


class KMCOIntegrationTests(unittest.TestCase):
    def test_exact_canonical_catalog(self):
        c = catalog()
        self.assertEqual(c.summary()["operations"], 132)
        self.assertEqual(c.summary()["groups"], 24)
        self.assertFalse(c.describe("P01-M01")["callable"])

    def test_unknown_or_fabricated_operation_denied(self):
        c = catalog()
        with self.assertRaises(KMCOError):
            c.describe("K01")
        with self.assertRaises(KMCOError):
            KMCOCatalog([], source_digests={"core.md": "a"*64, "domain.md": "b"*64})

    def test_dependency_order_and_cycle_guard(self):
        c = catalog()
        self.assertEqual(validate_route(c, [
            {"op_id": "P01-M02", "after": ["P01-M01"]},
            {"op_id": "P01-M01", "after": []}]), ["P01-M01", "P01-M02"])
        with self.assertRaisesRegex(KMCOError, "KMCO_ROUTE_CYCLE"):
            validate_route(c, [
                {"op_id": "P01-M01", "after": ["P01-M02"]},
                {"op_id": "P01-M02", "after": ["P01-M01"]}])
        with self.assertRaises(KMCOError):
            validate_route(c, [{"op_id": "P01-M01", "after": ["D99-M01"]}])

    def test_registry_integration_without_execution(self):
        reg = ToolRegistry()
        register_kmco_tools(reg, catalog())
        self.assertEqual(reg.names(), ["kmco.inspect", "kmco.plan"])
        ctx = {"evidence": []}
        item = reg.call("kmco.inspect", {"operation_id": "D01-M01"}, ctx)
        self.assertEqual(item["state"], "DESIGN_REGISTERED")
        self.assertFalse(item["authority_granted"])
        ordered = reg.call("kmco.plan", {"steps": [{"op_id": "D01-M01", "after": []}]}, ctx)
        self.assertFalse(ordered["executed"])
        self.assertEqual(len(ctx["evidence"]), 2)
        with self.assertRaises(ToolError):
            reg.call("kmco.execute", {}, ctx)


if __name__ == "__main__":
    unittest.main()
