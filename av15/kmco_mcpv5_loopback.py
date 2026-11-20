"""KMCO/MCP V5 loopback-only readback adapter.

Safe staging bridge for SDK v2.3.0. This does not grant control authority and
must NOT be forwarded to a public Cloudflare hostname without authenticated
client identity, authorization, and source/receipt verification.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "av15" / "state" / "kmco-mcpv5-local-acceptance.json"
MAX_BYTES = 256_000
mcp = MCPServer("KEBUNATE-MCPV5-LOCAL-READBACK", version="0.1.0")


@mcp.tool()
def kmco_local_acceptance() -> dict:
    """Read bounded, existing local KMCO/MCP V5 acceptance evidence only."""
    p = EVIDENCE.resolve(strict=True)
    if not p.is_relative_to((ROOT / "av15" / "state").resolve()):
        raise ValueError("EVIDENCE_SCOPE_DENIED")
    if p.stat().st_size > MAX_BYTES:
        raise ValueError("EVIDENCE_TOO_LARGE")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("EVIDENCE_INVALID")
    return {
        "read_only": True,
        "execution_authority": False,
        "evidence": data,
    }


if __name__ == "__main__":
    if os.environ.get("KMCO_LOOPBACK_CONFIRM") != "YES":
        raise SystemExit("BLOCKED: set KMCO_LOOPBACK_CONFIRM=YES for local test")
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8765,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=65536,
    )
