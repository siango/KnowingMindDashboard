"""Run reproducible KMCO design + resolver evidence against AV15 canonical snapshot.

Uses actual existing KMCW ToolRegistry, never a mocked execution handler.
Writes evidence JSON only to a user-selected output file, not to SSOT/DB.
Example:
 PYTHONPATH=av15 python3 av15/coding_worker_v2/kmco_evidence_demo.py \
   --source "$HOME/KnowingMind/workspaces/microops-source" \
   --output "$HOME/KnowingMind/workspaces/microops-source/kmco-test-evidence.json"
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from coding_worker_v2.agent.kmco_runtime import KMCOCatalog
from coding_worker_v2.agent.tools import ToolError, build_default_registry


def run(source: Path) -> dict:
    catalog = KMCOCatalog.from_snapshot(source)
    registry = build_default_registry(kmco_catalog=catalog)
    ctx = {"workspace": str(Path.cwd()), "evidence": []}

    # Concrete input for a real implemented dependency resolver, not 132 fake
    # operation handler executions. P04-M04 is the canonical DAG design step.
    input_case = {"steps": [
        {"op_id": "P04-M04", "after": ["P04-M03"]},
        {"op_id": "P04-M02", "after": ["P04-M01"]},
        {"op_id": "P04-M03", "after": ["P04-M02"]},
        {"op_id": "P04-M01", "after": []},
    ]}
    looked_up = registry.call("kmco.inspect", {"operation_id": "P04-M04"}, ctx)
    planned = registry.call("kmco.plan", input_case, ctx)
    expected = ["P04-M01", "P04-M02", "P04-M03", "P04-M04"]
    if planned["ordered_operations"] != expected or planned["executed"] is not False:
        raise AssertionError("KMCO_DAG_RESULT_MISMATCH")

    cycle_case = {"steps": [
        {"op_id": "P04-M01", "after": ["P04-M02"]},
        {"op_id": "P04-M02", "after": ["P04-M01"]},
    ]}
    try:
        registry.call("kmco.plan", cycle_case, ctx)
        raise AssertionError("KMCO_CYCLE_NOT_BLOCKED")
    except ToolError as exc:
        if exc.reason != "KMCO_ROUTE_CYCLE":
            raise
        cycle_error = exc.reason

    # No authority/binding in supplied context. A successful execution would
    # indicate a critical bug; require the existing gate to deny the request.
    try:
        registry.call("kmco.run_readonly", {"steps": [
            {"op_id": "P04-M04", "after": []}]}, ctx)
        raise AssertionError("KMCO_UNAUTHORIZED_EXECUTION")
    except ToolError as exc:
        if exc.reason != "KMCO_READ_EXECUTION_NOT_AUTHORIZED":
            raise
        denial = exc.reason

    calls = [e for e in ctx["evidence"] if e.get("event") == "tool_called"]
    if len(calls) != 4 or [e["ok"] for e in calls] != [True, True, False, False]:
        raise AssertionError("KMCO_AUDIT_MISMATCH")

    summary = catalog.summary()
    return {
        "schema": "KMCO_RUNTIME_EVIDENCE_V1",
        "source": {
            "operations": summary["operations"],
            "groups": summary["groups"],
            "source_sha256": summary["source_sha256"],
        },
        "real_code_path": [
            "coding_worker_v2.agent.kmco_runtime.KMCOCatalog",
            "coding_worker_v2.agent.kmco_runtime.validate_route",
            "coding_worker_v2.agent.tools.ToolRegistry.call",
        ],
        "case_valid_dag": {
            "operation_design_id": "P04-M04",
            "name": looked_up["name"],
            "input": input_case,
            "output": planned,
            "expected_order": expected,
            "pass": True,
        },
        "case_cycle": {"input": cycle_case, "error": cycle_error, "pass": True},
        "case_unauthorized": {
            "input": {"op_id": "P04-M04", "handler_binding": None},
            "error": denial, "pass": True,
        },
        "audit_events": calls,
        "actual_handler_executions": 0,
        "runtime_qualified_operations": 0,
        "independent_qc": "NOT_RUN",
        "receipt": None,
        "mcp_v5_live": "NOT_RUN",
        "database_write": False,
        "status": "CATALOG_AND_RESOLVER_TESTED_ONLY",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    doc = run(args.source)
    destination = args.output.expanduser().resolve()
    if destination.exists():
        raise SystemExit("BLOCKED: evidence file already exists; no overwrite")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    destination.write_bytes(encoded)
    print("KMCO_CATALOG", doc["source"]["operations"])
    print("KMCO_GROUPS", doc["source"]["groups"])
    print("VALID_DAG", doc["case_valid_dag"]["output"]["ordered_operations"])
    print("CYCLE_GUARD", doc["case_cycle"]["error"])
    print("AUTHORITY_GUARD", doc["case_unauthorized"]["error"])
    print("HANDLER_EXECUTIONS", doc["actual_handler_executions"])
    print("STATUS", doc["status"])
    print("EVIDENCE", destination)
    print("EVIDENCE_SHA256", hashlib.sha256(encoded).hexdigest())


if __name__ == "__main__":
    main()
