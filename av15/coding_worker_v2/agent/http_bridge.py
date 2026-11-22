"""Opt-in localhost-only MCP HTTP adapter over the installed KMCP ToolRegistry.

Never substitutes for the existing protected 18856 origin.  Does not issue
authority: tools/call is denied unless an independently supplied trusted
context producer qualifies the request, and write operations are disabled.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .mcp_server import MAX_MESSAGE, VERSIONS, _schemas, _catalog, _call
from .tools import ToolError


def read_credential(path):
    """Require an existing private regular file owned by the invoking UID."""
    path = Path(path)
    meta = path.lstat()
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
            or meta.st_mode & 0o077):
        raise PermissionError("KMCP_HTTP_CREDENTIAL_SCOPE_DENIED")
    secret = path.read_bytes()
    if not 32 <= len(secret) <= 1024 or b"\n" in secret or b"\r" in secret:
        raise ValueError("KMCP_HTTP_CREDENTIAL_INVALID")
    return secret


def handler_factory(ctx, registry, secret, *, context_provider=None):
    """The caller may inject only a trusted in-process issuer, never JSON claims."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "KMCP-HTTP-Bridge"
        sys_version = ""
        def log_message(self, *_args):
            pass  # Never write authorization headers, arguments or results to logs.

        def reply(self, status, body=None):
            raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if raw:
                self.wfile.write(raw)

        def authorized(self):
            given = self.headers.get("Authorization", "")
            if (not given.startswith("Bearer ") or
                    not hmac.compare_digest(given[7:].encode(), secret)):
                self.reply(401, {"error": "AUTH_REQUIRED"})
                return False
            return True

        def do_GET(self):
            if self.path != "/mcp":
                self.reply(404)
            elif not self.authorized():
                return
            else:
                self.reply(405, {"error": "POST_REQUIRED"})

        def do_POST(self):
            if self.path != "/mcp":
                return self.reply(404)
            if not self.authorized():
                return
            size = self.headers.get("Content-Length")
            if size is None or not size.isdecimal() or int(size) > MAX_MESSAGE:
                return self.reply(413, {"error": "MCP_BODY_INVALID"})
            try:
                request = json.loads(self.rfile.read(int(size)))
            except (UnicodeDecodeError, ValueError):
                return self.reply(400, {"error": "INVALID_JSON"})
            if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                    or type(request.get("id")) not in (str, int)
                    or not isinstance(request.get("method"), str)
                    or not isinstance(request.get("params", {}), dict)):
                return self.reply(400, {"error": "INVALID_RPC"})
            method, params = request["method"], request.get("params", {})
            answer = {"jsonrpc": "2.0", "id": request["id"]}
            if method == "initialize":
                ver = params.get("protocolVersion")
                answer["result"] = {
                    "protocolVersion": ver if ver in VERSIONS else "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "kmcp-http-optin", "version": "0.1.0"}}
            elif method == "ping":
                answer["result"] = {}
            elif method == "tools/list":
                schemas = _schemas(registry, ctx)
                catalog = _catalog(registry, {**ctx, "read_only": True}, schemas)
                rows = {x["name"]: x for x in catalog["tools"]}
                for item in schemas:
                    item["_meta"]["kmcw/capability"] = rows[item["name"]]
                answer["result"] = {"tools": schemas}
            elif method == "tools/call":
                try:
                    name = params.get("name")
                    specs = {x["name"]: x for x in registry.command_contract()["commands"]}
                    if name not in specs or specs[name]["permission"] != "read":
                        raise ToolError("KMCP_HTTP_READ_ONLY_DENIED")
                    if context_provider is None:
                        raise ToolError("KMCP_HTTP_TRUSTED_CONTEXT_REQUIRED")
                    from .capabilities import bind_runtime_context
                    call_ctx = bind_runtime_context({**ctx, "read_only": True},
                                                     context_provider)
                    if call_ctx.get("read_only") is not True:
                        raise ToolError("KMCP_HTTP_CONTEXT_WRITE_DENIED")
                    result = _call(registry, params, call_ctx)
                    answer["result"] = {"content": [{
                        "type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                        "isError": False}
                except (ToolError, OSError, ValueError) as exc:
                    answer["result"] = {"content": [{
                        "type": "text", "text": json.dumps({"ok": False,
                            "reason": getattr(exc, "reason", type(exc).__name__)})}],
                            "isError": True}
            else:
                answer["error"] = {"code": -32601, "message": "Method not found"}
            self.reply(200, answer)
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--auth-file", required=True,
                        help="Existing owner-only bearer credential; no credential creation")
    parser.add_argument("--port", type=int, default=18857)
    parser.add_argument("--admin-runtime-context", action="store_true",
                        help="Use existing verified KM AI Admin read-only issuer; no new authority")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("invalid port")
    from .circuit_cli import load_runtime
    ctx, registry = load_runtime(args.config)
    secret = read_credential(args.auth_file)
    from .capabilities import KMAdminContextProducer
    issuer = KMAdminContextProducer.runtime_context if args.admin_runtime_context else None
    with ThreadingHTTPServer(("127.0.0.1", args.port),
          handler_factory(ctx, registry, secret, context_provider=issuer)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
