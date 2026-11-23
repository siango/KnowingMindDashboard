"""KMCO contract and existing ToolRegistry integration: fail closed."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from coding_worker_v2.agent.kmco_runtime import KMCOCatalog, KMCOError, validate_route, register_kmco_tools
from coding_worker_v2.agent.tools import ToolRegistry, ToolError


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
        self.assertEqual(reg.names(), ["kmco.core.recall", "kmco.d01.authority", "kmco.d02.read", "kmco.d06.question_preview", "kmco.d07.memory_preview", "kmco.d08.preflight", "kmco.d09.preflight", "kmco.d10.drift_preview", "kmco.d11.observe", "kmco.d12.backup_restore", "kmco.inspect", "kmco.p01.evidence", "kmco.p02.resume", "kmco.p03.loop_guard", "kmco.p04.plan", "kmco.p05.completion", "kmco.p06.permission", "kmco.p07.checkpoint", "kmco.p08.memory", "kmco.p09.lease", "kmco.p10.route", "kmco.p11.security", "kmco.p12.providers", "kmco.plan", "kmco.run_readonly"])
        ctx = {"evidence": []}
        item = reg.call("kmco.inspect", {"operation_id": "D01-M01"}, ctx)
        self.assertEqual(item["state"], "DESIGN_REGISTERED")
        self.assertFalse(item["authority_granted"])
        ordered = reg.call("kmco.plan", {"steps": [{"op_id": "D01-M01", "after": []}]}, ctx)
        self.assertFalse(ordered["executed"])
        self.assertEqual(sum(e.get("event") == "tool_called" for e in ctx["evidence"]), 2)
        with self.assertRaises(ToolError):
            reg.call("kmco.run_readonly", {"steps": [{"op_id": "D01-M01", "after": []}]}, ctx)
        with self.assertRaises(ToolError):
            reg.call("kmco.execute", {}, ctx)
        with self.assertRaises(ToolError):
            reg.call("kmco.d08.preflight", {"envelope": {}}, ctx, mcp=True)
        with self.assertRaises(ToolError):
            reg.call("kmco.d09.preflight", {"output": {}}, ctx, mcp=True)
        for name, args in (
            ("kmco.d11.observe", {"request": {}}),
            ("kmco.d07.memory_preview", {"goal": "test", "limit": 1}),
            ("kmco.d10.drift_preview", {"source": {}, "projection": {}}),
            ("kmco.d06.question_preview", {"request": {}}),
        ):
            with self.subTest(name=name), self.assertRaises(ToolError):
                reg.call(name, args, ctx, mcp=True)


    def test_p02_permanent_registry_runs_real_five_steps(self):
        from unittest import mock
        reg = ToolRegistry()
        register_kmco_tools(reg, catalog())
        sha = "a" * 40
        previous = {"mission_id": "KMCP-V5-PRODUCTION",
                    "task_id": "owner-p02-acceptance",
                    "scope": ["read-only"], "constraints": ["no writes"],
                    "source_sha": sha}
        current = dict(previous, constraints=["no writes", "bounded operations"])
        ctx = {"workspace": "/verified/fixture-workspace",
               "mission_id": previous["mission_id"],
               "task_id": previous["task_id"],
               "read_only": True, "evidence": []}
        with mock.patch("coding_worker_v2.agent.memory.current_base_commit",
                        return_value=sha):
            result = reg.call("kmco.p02.resume",
                              {"previous": previous, "current": current}, ctx)
            self.assertEqual(result["steps_completed"],
                             [f"P02-M{i:02d}" for i in range(1, 6)])
            self.assertFalse(result["authority_granted"])
            self.assertFalse(result["receipt_verified"])
            # Caller-provided identity is not trusted as the issuer.
            with self.assertRaisesRegex(ToolError, "KMCO_P02_ISSUER_SCOPE_MISMATCH"):
                reg.call("kmco.p02.resume",
                         {"previous": dict(previous, mission_id="another"),
                          "current": current}, ctx)
            # Invalid scope must not be normalized silently.
            with self.assertRaisesRegex(ToolError, "P02_CONSTRAINT_LOSS"):
                reg.call("kmco.p02.resume",
                         {"previous": current, "current": previous}, ctx)
            with self.assertRaisesRegex(ToolError, "KMCO_P02_SOURCE_CHANGED"):
                reg.call("kmco.p02.resume",
                         {"previous": previous,
                          "current": dict(current, source_sha="b" * 40)}, ctx)
            # Even a registered real read handler is not automatically an
            # authenticated MCP read-only tool call.
            with self.assertRaisesRegex(ToolError, "MCP_TOOL_SCOPE_DENIED"):
                reg.call("kmco.p02.resume",
                         {"previous": previous, "current": current}, ctx, mcp=True)

    def test_p01_permanent_registry_executes_real_file(self):
        reg = ToolRegistry()
        register_kmco_tools(reg, catalog())
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "evidence.txt"
            source.write_text(chr(10).join(["one", "verified line", "three", ""]), encoding="utf-8")
            ctx = {"workspace": d, "read_only": True, "evidence": []}
            args = {"file": "evidence.txt", "quote": "verified line"}
            result = reg.call("kmco.p01.evidence", args, ctx)
            self.assertEqual(result["steps_completed"], [
                "P01-M01", "P01-M02", "P01-M03", "P01-M04", "P01-M05"])
            self.assertEqual(result["output"]["citation"]["line"], 2)
            self.assertEqual(
                result["output"]["citation"]["sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertFalse(result["output"]["semantic_truth_verified"])
            with self.assertRaisesRegex(ToolError, "KMCO_P01_DENIED"):
                reg.call("kmco.p01.evidence",
                         {"file": "evidence.txt", "quote": "not present"}, ctx)
            with self.assertRaisesRegex(ToolError, "KMCO_P01_DENIED"):
                reg.call("kmco.p01.evidence",
                         {"file": "../outside.txt", "quote": "verified line"}, ctx)
            # A local circuit pass is never an MCP authorization grant.
            with self.assertRaisesRegex(ToolError, "MCP_TOOL_SCOPE_DENIED"):
                reg.call("kmco.p01.evidence", args, ctx, mcp=True)
            self.assertEqual(
                [e["ok"] for e in ctx["evidence"] if e.get("tool") == "kmco.p01.evidence"
                 and e.get("event") == "tool_called"], [True, False, False, False])


if __name__ == "__main__":
    unittest.main()
