#!/usr/bin/env python3
"""Run ACTUAL AccessGate source without relying on optional MCP SDK installation.

This checks the transport guard only; NEVER implies live Cloudflare or connector
authentication. No secrets, sockets, or production mutations.
"""
import ast
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[2] / "av15/mcpv5_secure_http_readback.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AccessGate")
assert node.bases == [], "Unexpected inheritance; update source test rather than bypassing it"
sandbox = {"__name__": "kmcp_access_gate_source_test"}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), sandbox)
AccessGate = sandbox["AccessGate"]


def transact(headers=(), *, token_valid=False):
    seen = []
    calls = []
    def verifier(value):
        seen.append(value)
        if not token_valid or value != "access-jwt":
            raise ValueError("INVALID_ACCESS_JWT")
        return {"sub": "fixture"}
    sandbox["validate_access_jwt"] = verifier
    async def inner(scope, receive, send):
        calls.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    events = []
    async def send(event):
        events.append(event)
    asyncio.run(AccessGate(inner)(
        {"type": "http", "path": "/mcp", "headers": list(headers)}, receive, send))
    return events[0]["status"], calls, seen


class AccessGateSourceTests(unittest.TestCase):
    def test_anonymous_fails_closed(self):
        self.assertEqual(transact()[0], 401)

    def test_distinct_oauth_and_signed_access_pass_transport_only(self):
        status, called, seen = transact([
            (b"authorization", b"Bearer oauth-client-token"),
            (b"cf-access-jwt-assertion", b"access-jwt")], token_valid=True)
        self.assertEqual(status, 200)
        self.assertEqual(called, [True])
        self.assertEqual(seen, ["access-jwt"])

    def test_signed_access_failure_denied_even_with_oauth(self):
        status, called, _ = transact([
            (b"authorization", b"Bearer oauth-client-token"),
            (b"cf-access-jwt-assertion", b"invalid")], token_valid=True)
        self.assertEqual((status, called), (401, []))

    def test_oauth_only_does_not_grant_access(self):
        status, called, _ = transact([
            (b"authorization", b"Bearer oauth-client-token")], token_valid=True)
        self.assertEqual((status, called), (401, []))

    def test_duplicate_assertion_and_bearer_denied(self):
        for name, token in (
            (b"authorization", b"Bearer access-jwt"),
            (b"cf-access-jwt-assertion", b"access-jwt")
        ):
            with self.subTest(name=name):
                status, called, _ = transact([(name, token), (name, token)], token_valid=True)
                self.assertEqual((status, called), (401, []))

    def test_legacy_signed_access_jwt_bearer_still_accepted(self):
        status, called, seen = transact([
            (b"authorization", b"Bearer access-jwt")], token_valid=True)
        self.assertEqual((status, called, seen), (200, [True], ["access-jwt"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
