#!/usr/bin/env python3
"""Read-only KMCO/MCP V5 local acceptance. Never grants execution authority."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from coding_worker_v2.agent.kmco_runtime import KMCOCatalog
from coding_worker_v2.agent.tools import build_default_registry

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--catalog-root", required=True)
    ap.add_argument("--output", default="/tmp/kmco_mcpv5_acceptance.json")
    args = ap.parse_args()
    workspace = Path(args.workspace).resolve(strict=True)
    catalog = KMCOCatalog.from_snapshot(args.catalog_root)
    summary = catalog.summary()
    registry = build_default_registry(kmco_catalog=catalog)
    registered = {r["name"] for r in registry.command_contract()["commands"]}
    gateways = {"kmco.inspect", "kmco.plan", "kmco.run_readonly"}
    assert gateways <= registered, "GATEWAY_MISSING"
    for op in catalog._operations:
        item = registry.call("kmco.inspect", {"operation_id": op}, {"evidence": []})
        assert item["operation_id"] == op
        assert item["callable"] is False
    steps = [{"op_id": f"P01-M{i:02d}",
              "after": [] if i == 1 else [f"P01-M{i-1:02d}"]}
             for i in range(1, 6)]
    plan = registry.call("kmco.plan", {"steps": steps}, {"evidence": []})
    assert plan["ordered_operations"] == [f"P01-M{i:02d}" for i in range(1, 6)]
    assert plan["executed"] is False
    messages = [
      {"jsonrpc":"2.0","id":1,"method":"initialize","params":{
       "protocolVersion":"2025-03-26","capabilities":{},
       "clientInfo":{"name":"kmco-local-acceptance","version":"1"}}},
      {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
    ]
    env = dict(os.environ, KMCO_CATALOG_ROOT=str(Path(args.catalog_root).resolve()),
               PYTHONPATH=str(workspace / "av15"), PYTHONDONTWRITEBYTECODE="1")
    # Use only the existing stdio MCP entry and never call tools/call.
    p = subprocess.run(
      [sys.executable, "-m", "coding_worker_v2.agent.mcp_server",
       "--base", str(workspace / "av15/coding_worker_v2"),
       "--workspace", str(workspace)],
      input="".join(json.dumps(m)+"\n" for m in messages), text=True,
      capture_output=True, cwd=workspace, env=env, timeout=30)
    if p.returncode:
        raise RuntimeError("MCP_STDIO_FAILED: " + p.stderr[-500:])
    replies = [json.loads(s) for s in p.stdout.splitlines() if s.strip()]
    init = next((x for x in replies if x.get("id")==1), None)
    listing = next((x for x in replies if x.get("id")==2), None)
    assert init and "result" in init, "MCP_INITIALIZE_FAILED"
    assert listing and "result" in listing, "MCP_LIST_FAILED"
    names = {x["name"] for x in listing["result"].get("tools", [])}
    assert gateways <= names, "MCP_GATEWAYS_MISSING"
    report = {
      "schema":"KMCO_MCPV5_LOCAL_ACCEPTANCE_V1",
      "catalog_operations":summary["operations"],
      "catalog_groups":summary["groups"],
      "catalog_integrity":"PASS",
      "tool_registry_inspect_pass":len(catalog._operations),
      "p01_plan":"PASS",
      "mcp_initialize":"PASS",
      "mcp_gateway_count":len(gateways & names),
      "mcp_total_discovered":len(names),
      "runtime_kind":"EPHEMERAL_STDIO",
      "mcp_external_route":"NOT_TESTED",
      "authenticated_execution":"NOT_RUN",
      "independent_qc":"NOT_RUN",
      "verified_receipt":False,
      "production_callable_verified":False,
      "full_132_handlers":"NOT_IMPLEMENTED"
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps(report,indent=2))
    print("EVIDENCE_PATH:",out)
    return 0
if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print("KMCO_MCPV5_ACCEPTANCE_BLOCKED:", type(exc).__name__,str(exc)[:500],file=sys.stderr)
        raise SystemExit(2)
