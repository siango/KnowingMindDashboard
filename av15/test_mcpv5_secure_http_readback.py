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


def test_oauth_bearer_and_signed_access_assertion_are_separate():
    # The OAuth bearer authenticates the client at the application layer.
    # Cloudflare injects a DISTINCT signed Access JWT for transport identity.
    events, called = dispatch([
        (b"authorization", b"Bearer oauth-fixture"),
        (b"cf-access-jwt-assertion", b"access-jwt-fixture")], authorized=True)
    assert events[0]["status"] == 200
    assert called == ["app_called"]


def test_invalid_access_assertion_denied_even_with_oauth_bearer():
    events, called = dispatch([
        (b"authorization", b"Bearer oauth-fixture"),
        (b"cf-access-jwt-assertion", b"invalid")], authorized=False)
    assert events[0]["status"] == 401
    assert called == []


def test_duplicate_security_headers_denied_even_when_token_valid():
    for name, value in [
        (b"authorization", b"Bearer token"),
        (b"cf-access-jwt-assertion", b"signed-token")
    ]:
        events, called = dispatch([
            (name, value), (name, value)], authorized=True)
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

