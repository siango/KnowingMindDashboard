"""Safety tests for isolated JWT-gated MCP HTTP readback origin."""
import asyncio
from unittest import mock

from av15 import mcpv5_secure_http_readback as gateway


def dispatch(headers=(), path="/mcp", authorized=False):
    calls = []
    async def inner(scope, receive, send):
        calls.append("app_called")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    events = []
    async def send(event):
        events.append(event)

    gate = gateway.AccessGate(inner)
    async def run():
        await gate({"type": "http", "path": path,
                    "headers": list(headers)}, receive, send)

    with mock.patch.object(gateway, "validate_access_jwt") as verify:
        if authorized:
            verify.return_value = {"sub": "test"}
        else:
            verify.side_effect = ValueError("denied")
        asyncio.run(run())
    return events, calls


def test_anonymous_denied():
    events, called = dispatch()
    assert events[0]["status"] == 401
    assert called == []


def test_invalid_token_denied():
    events, called = dispatch([(b"authorization", b"Bearer invalid")])
    assert events[0]["status"] == 401
    assert called == []


def test_conflicting_token_headers_denied():
    events, called = dispatch([
        (b"authorization", b"Bearer foo"),
        (b"cf-access-jwt-assertion", b"bar")], authorized=True)
    assert events[0]["status"] == 401
    assert called == []


def test_wrong_path_denied():
    events, called = dispatch(path="/admin", authorized=True)
    assert events[0]["status"] == 404
    assert called == []


def test_validated_identity_reaches_readback_only():
    events, called = dispatch([(b"authorization", b"Bearer fixture")], authorized=True)
    assert events[0]["status"] == 200
    assert called == ["app_called"]

