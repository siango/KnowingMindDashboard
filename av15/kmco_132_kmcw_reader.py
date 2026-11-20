"""Read-only KMCO 132 design and KMCW tool contract report.

This is an inspection utility, not a new KMCO runtime, MCP server, or
permission grant. It reads an owner-supplied pinned design snapshot and
the existing KMCW ToolRegistry's source contract. It never calls handlers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from coding_worker_v2.agent.kmco_runtime import KMCOCatalog, KMCOError
from coding_worker_v2.agent.tools import build_default_registry


def _report(root: Path, operation_id: str | None = None) -> dict:
    # The existing validator checks that the catalog contains exactly 132
    # distinct operations in 24 groups and matches canonical source digests.
    catalog = KMCOCatalog.from_snapshot(root)
    path = root / "kmco-registry-candidate.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    records = doc["records"]
    if operation_id is not None:
        catalog.describe(operation_id)  # Fail closed for an unknown ID.
        records = [r for r in records if r["operation_id"] == operation_id]

    registry = build_default_registry()
    contract = registry.command_contract()  # SOURCE ONLY, no tool execution.
    rows = []
    for r in sorted(records, key=lambda item: item["operation_id"]):
        description = catalog.describe(r["operation_id"])
        rows.append({
            **description,
            "source_file": r["source_file"],
            "source_cells": r["source_cells"],
            "handler_binding": "NOT_EVIDENCED_IN_DESIGN_SNAPSHOT",
            "execution_verified": False,
        })
    return {
        "schema": "KMCO_132_KMCW_SOURCE_READER_V1",
        "status": "SOURCE_INSPECTION_ONLY",
        "operation_count_in_catalog": catalog.summary()["operations"],
        "operation_count_returned": len(rows),
        "group_count_in_catalog": catalog.summary()["groups"],
        "catalog_source_sha256": catalog.summary()["source_sha256"],
        "kmcw_registry_contract_sha256": contract["sha256"],
        "kmcw_registered_tool_count": contract["command_count"],
        "operations": rows,
        "kmcw_tools": [
            {
                "name": item["name"],
                "capability": item["capability"],
                "permission": item["permission"],
                "limits": item["limits"],
                "source_ref": item["source_ref"],
                "backend_ref": item["backend_ref"],
            }
            for item in contract["commands"]
        ],
        "live_runtime_checked": False,
        "handlers_invoked": False,
        "authorization_granted": False,
        "independent_qc": "NOT_RUN",
    }


def _markdown(report: dict) -> str:
    out = [
        "# KMCO 132 Logic — KMCW Read-only Source Report",
        "",
        f"Catalog: {report['operation_count_in_catalog']} operations / "
        f"{report['group_count_in_catalog']} groups",
        f"KMCW registered tool contracts: {report['kmcw_registered_tool_count']}",
        f"Catalog source SHA256: {json.dumps(report['catalog_source_sha256'], ensure_ascii=False)}",
        f"KMCW contract SHA256: \`{report['kmcw_registry_contract_sha256']}\`",
        "",
        "**Evidence scope:** source inspection only. No handler calls, live "
        "readiness, authority, QC or successful execution is implied.",
        "",
        "## KMCO operations",
        "",
    ]
    last_group = None
    for row in report["operations"]:
        if row["group_id"] != last_group:
            last_group = row["group_id"]
            out.extend([f"### {last_group}", ""])
        out.append(f"**{row['operation_id']} — {row['name']}**")
        out.append(f"- Source: \`{row['source_file']}\` | SHA256: \`{row['source_sha256']}\`")
        out.append("- Source cells: " + json.dumps(row["source_cells"], ensure_ascii=False))
        out.append("- KMCW handler mapping: not evidenced; design only")
        out.append("")
    out.extend(["## KMCW ToolRegistry — declared source contract", ""])
    for tool in report["kmcw_tools"]:
        out.append(f"**{tool['name']}** — {tool['capability']}")
        out.append(f"- Permission: \`{tool['permission']}\`; limits: {tool['limits'] or 'not specified'}")
        out.append(f"- Source: \`{tool['source_ref']}\`")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog-root", type=Path, required=True,
        help="Existing pinned directory containing core.md, domain.md and "
             "kmco-registry-candidate.json",
    )
    parser.add_argument("--operation-id", help="Optional specific Pxx-Mxx / Dxx-Mxx ID")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    try:
        result = _report(args.catalog_root, args.operation_id)
    except (KMCOError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"KMCO_READER_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
