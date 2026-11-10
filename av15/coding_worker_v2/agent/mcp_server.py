"""Bounded newline-delimited MCP stdio transport over ToolRegistry."""
from __future__ import annotations

import json
import sys

from .pilot import TOOLS
from .tools import ToolError, build_default_registry

MAX_MESSAGE = 1_000_000
VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
JSON_TYPES = {"str": "string", "int": "integer", "bool": "boolean",
              "list": "array", "dict": "object"}


def _schemas(registry):
    return [{
        "name": spec["name"], "description": spec["capability"] + ". " + spec["limits"],
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "required": list(spec["required"]),
            "properties": {key: {"type": JSON_TYPES[value]} for key, value in
                           {**spec["required"], **spec["optional"]}.items()},
        },
    } for spec in registry.command_contract()["commands"] if spec["name"] in TOOLS]


def _call(registry, params, ctx):
    if not isinstance(params, dict) or params.get("name") not in TOOLS:
        raise ToolError("PILOT_TOOL_NOT_EXPOSED")
    ctx["evidence"] = []
    try:
        return registry.call(params["name"], params.get("arguments", {}), ctx)
    finally:
        with ctx["store"]._locked():
            saved = ctx["store"].load_checkpoint(".tool-audit")
            if saved is None and (ctx["store"].checkpoints / ".tool-audit.json").exists():
                raise ValueError("tool approval audit unreadable")
            events = (saved or {}).get("data", {}).get("events", [])
            ctx["store"].save_checkpoint(".tool-audit", "tool_policy_audit", {
                "events": events + [{**event, "account_id": ctx["account_id"]}
                                   for event in ctx["evidence"]]})


def serve(ctx, source=None, sink=None):
    source = source or sys.stdin.buffer
    sink = sink or sys.stdout
    registry = build_default_registry()
    initialized = False
    while True:
        raw = source.readline(MAX_MESSAGE + 1)
        if not raw:
            return 0
        if len(raw) > MAX_MESSAGE:
            print("KMCW_MCP_MESSAGE_TOO_LARGE: transport closed", file=sys.stderr)
            return 1
        request = None
        try:
            request = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}
        else:
            if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                    or not isinstance(request.get("method"), str)
                    or ("id" in request and type(request["id"]) not in (str, int))):
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32600, "message": "Invalid Request"}}
            elif "id" not in request:
                continue
            else:
                response = {"jsonrpc": "2.0", "id": request["id"]}
                method = request["method"]
                params = request.get("params", {})
                if not isinstance(params, dict):
                    response["error"] = {"code": -32602, "message": "Expected object params"}
                elif method == "initialize":
                    version = params.get("protocolVersion")
                    response["result"] = {
                        "protocolVersion": version if version in VERSIONS else "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "kmcw", "version": "0.1.0-internal"}}
                    initialized = True
                elif not initialized:
                    response["error"] = {"code": -32000, "message": "Initialize first"}
                elif method == "ping":
                    response["result"] = {}
                elif method == "tools/list":
                    response["result"] = {"tools": _schemas(registry)}
                elif method == "tools/call":
                    try:
                        result = _call(registry, params, ctx)
                        response["result"] = {"content": [{
                            "type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                            "isError": False}
                    except (ToolError, ValueError, OSError) as exc:
                        reason = exc.reason if isinstance(exc, ToolError) else type(exc).__name__
                        print(f"KMCW_MCP_CALL_FAILED: {reason}", file=sys.stderr)
                        response["result"] = {"content": [{
                            "type": "text", "text": json.dumps({"ok": False, "reason": reason})}],
                            "isError": True}
                else:
                    response["error"] = {"code": -32601, "message": "Method not found"}
        sink.write(json.dumps(response, ensure_ascii=False) + "\n")
        sink.flush()
