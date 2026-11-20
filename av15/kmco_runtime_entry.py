#!/usr/bin/env python3
"""KMCO local runtime entry: pinned catalog, plan and P01 read-only execution.

This entry intentionally does not grant KMCW/MCP execution authority, create
handlers for DESIGN_ONLY records, or claim independent QC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

AV15 = Path(__file__).resolve().parent
sys.path.insert(0, str(AV15))
from coding_worker_v2.agent.kmco_runtime import KMCOCatalog, validate_route
from coding_worker_v2.agent.kmco_p01 import run_p01


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def load_catalog(directory: str) -> KMCOCatalog:
    if not directory:
        raise ValueError("KMCO_CATALOG_ROOT_REQUIRED")
    return KMCOCatalog.from_snapshot(Path(directory))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="KMCO bounded local runtime")
    parser.add_argument("--catalog-root", default=os.getenv("KMCO_CATALOG_ROOT", ""))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    p = sub.add_parser("inspect")
    p.add_argument("operation_id")
    p = sub.add_parser("plan")
    p.add_argument("--steps-json", required=True)
    p = sub.add_parser("p01")
    p.add_argument("--workspace", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--quote", required=True)
    p.add_argument("--assertions-json", default="[]")
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.catalog_root)
        if args.command == "status":
            summary = catalog.summary()
            emit({"schema": "KMCO_LOCAL_RUNTIME_STATUS_V1", **summary,
                  "gateway": "EXISTING_KMCW_TOOLREGISTRY",
                  "p01_local_functional": True,
                  "p01_authenticated_execution": "NOT_VERIFIED",
                  "independent_qc": "NOT_RUN",
                  "production_authority": False,
                  "design_only_operations": summary["operations"]})
        elif args.command == "inspect":
            emit(catalog.describe(args.operation_id))
        elif args.command == "plan":
            steps = json.loads(args.steps_json)
            emit({"status": "PLAN_ONLY", "ordered_operations": validate_route(catalog, steps),
                  "executed": False, "independent_qc": "NOT_RUN"})
        elif args.command == "p01":
            workspace = Path(args.workspace).resolve(strict=True)
            if not workspace.is_dir():
                raise ValueError("KMCO_WORKSPACE_INVALID")
            assertions = json.loads(args.assertions_json)
            if not isinstance(assertions, list):
                raise ValueError("KMCO_ASSERTIONS_INVALID")
            outcome = run_p01(workspace, args.file, args.quote, assertions)
            emit({"schema": "KMCO_P01_LOCAL_EXECUTION_V1",
                  "status": "EXECUTED_UNVERIFIED", "execution_scope": "LOCAL_READ_ONLY",
                  "workspace": str(workspace), "result": outcome,
                  "independent_qc": "NOT_RUN", "receipt_verified": False,
                  "production_authority": False})
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        emit({"ok": False, "status": "BLOCKED",
              "reason": str(exc)[:240], "production_authority": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
