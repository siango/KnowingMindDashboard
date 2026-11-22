"""KMCW -> existing local MCP stdio protocol acceptance; never grants authority.

Exact in-process transport of the existing mcp_server.serve() using the already
loaded KMCW ToolRegistry; no HTTP origin, token, new listener or queue.
"""
from __future__ import annotations
import io
import json
from .mcp_server import serve
from .kmco_runtime import validate_route, KMCOError

def inspect_local(ctx, registry):
    if not isinstance(ctx, dict) or registry is None:
        raise ValueError("LOCAL_MCP_RUNTIME_REQUIRED")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    source = io.BytesIO(("\n".join(json.dumps(q) for q in requests)+"\n").encode("utf-8"))
    output = io.StringIO()
    code = serve(ctx, source=source, sink=output, context_provider=None)
    responses = [json.loads(s) for s in output.getvalue().splitlines()]
    if (code != 0 or len(responses) != 2 or responses[0].get("id") != 1
            or responses[1].get("id") != 2
            or responses[0].get("result", {}).get("protocolVersion") != "2025-06-18"):
        raise ValueError("LOCAL_MCP_INITIALIZE_FAILED")
    result = responses[1].get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        raise ValueError("LOCAL_MCP_TOOLS_LIST_FAILED")
    names = [t.get("name") for t in result["tools"]]
    if (any(not isinstance(n, str) or not n for n in names)
            or len(names) != len(set(names))):
        raise ValueError("LOCAL_MCP_DUPLICATE_OR_INVALID_TOOL")
    registered = registry.command_contract()["commands"]
    contract_names = {t["name"] for t in registered}
    missing = sorted(contract_names - set(names))
    meta = result.get("_meta", {}).get("kmcw/capabilities", {})
    catalog = ctx.get("kmco_catalog")
    kmco = {"status": "NOT_BOUND", "expected_operations": 132, "expected_groups": 24,
            "design_routes_checked": 0, "executable_routes_verified": 0}
    if catalog is not None:
        summary = catalog.summary()
        if summary.get("operations") != 132 or summary.get("groups") != 24:
            raise ValueError("KMCO_SOURCE_COUNT_INVALID")
        for op_id in sorted(catalog._operations):
            description = catalog.describe(op_id)
            if (description.get("callable") is not False or
                    validate_route(catalog, [{"op_id": op_id, "after": []}]) != [op_id]):
                raise ValueError("KMCO_SINGLE_OP_DESIGN_INVALID")
        kmco.update(status="DESIGN_VALIDATED_ONLY", design_routes_checked=132,
                    operations=summary["operations"], groups=summary["groups"])
    return {
        "schema": "KMCW_LOCAL_MCP_PROTOCOL_READBACK_V1",
        "transport": "IN_PROCESS_EXISTING_MCP_STDIO",
        "initialize": "PASS",
        "tools_list": "PASS",
        "registered_count": len(registered),
        "advertised_count": len(names),
        "missing_registered_names": missing,
        "reported_callable_count": meta.get("callable_count"),
        "kmco": kmco,
        "authenticated_http_origin": "NOT_TESTED",
        "access_jwt": "NOT_TESTED",
        "issuer_delegation": "NOT_TESTED",
        "tool_calls_executed": 0,
        "independent_qc": "NOT_RUN",
        "production_acceptance": False
    }
