"""KEBUNATE MCP V5 authenticated origin (READ-ONLY staging).

This is NOT a production KMCW command gateway. All KMCW execution stays
disabled until a per-request authenticated delegation and independent QC exist.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "av15/state/kmco-mcpv5-protocol-checkpoint.json"
ISSUER = "https://bitter-king-9b22.cloudflareaccess.com"
AUDIENCE = "8a8ac2eecb2c188a99540e9edfc92b0059a09706cf1e767b843c91ccda3b2e8f"
HOST = "127.0.0.1"
PORT = 18856

mcp = MCPServer("KEBUNATE-MCPV5-AUTHENTICATED-READBACK", version="0.1.0")

@mcp.tool()
def kmcw_readiness() -> dict:
    """Read a bounded local checkpoint; never authorize or dispatch tasks."""
    import json
    p = EVIDENCE.resolve(strict=True)
    if not p.is_relative_to((ROOT / "av15/state").resolve()):
        raise ValueError("EVIDENCE_SCOPE_DENIED")
    if p.stat().st_size > 32768:
        raise ValueError("EVIDENCE_TOO_LARGE")
    record = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("EVIDENCE_INVALID")
    return {"read_only": True, "execution_authority": False,
            "checkpoint": record}

def validate_access_jwt(raw: str) -> dict:
    """Verify issuer, audience, signature, expiry and exact allowed subject."""
    import jwt
    if not isinstance(raw, str) or len(raw) > 8192 or not raw:
        raise ValueError("ACCESS_TOKEN_MISSING")
    subject = os.getenv("KEBUNATE_ACCESS_SUB", "")
    if not subject or len(subject) > 256:
        raise ValueError("ACCESS_SUB_NOT_CONFIGURED")
    jwks = jwt.PyJWKClient(ISSUER + "/cdn-cgi/access/certs",
                           cache_keys=True, lifespan=300,
                           timeout=5)
    key = jwks.get_signing_key_from_jwt(raw)
    claims = jwt.decode(raw, key.key, algorithms=["RS256"],
                        audience=AUDIENCE, issuer=ISSUER,
                        options={"require": ["sub", "exp", "iat", "iss", "aud"]},
                        leeway=10)
    if claims.get("sub") != subject:
        raise ValueError("ACCESS_SUB_DENIED")
    if claims.get("type") not in ("app",):
        raise ValueError("ACCESS_TOKEN_TYPE_DENIED")
    return claims

class AccessGate:
    """Pure ASGI outer middleware, BEFORE any MCP message processing."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path != "/mcp":
            return await self._reject(send, 404)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        # Require an actual signed Access JWT; never trust identity headers.
        supplied = headers.get(b"cf-access-jwt-assertion", b"")
        auth = headers.get(b"authorization", b"")
        if auth.startswith(b"Bearer "):
            bearer = auth[7:]
            if supplied and supplied != bearer:
                return await self._reject(send, 401)
            supplied = bearer
        try:
            validate_access_jwt(supplied.decode("ascii"))
        except Exception:
            return await self._reject(send, 401)
        return await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": b'{"ok":false}'})

def main():
    if os.getenv("KEBUNATE_HTTP_READBACK_CONFIRM") != "YES":
        raise SystemExit("BLOCKED: EXPLICIT_LOCAL_CONFIRM_REQUIRED")
    if not os.getenv("KEBUNATE_ACCESS_SUB"):
        raise SystemExit("BLOCKED: EXACT_ALLOWED_ACCESS_SUB_REQUIRED")
    try:
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        import uvicorn
    except ImportError as exc:
        raise SystemExit("BLOCKED: INSTALL_PYJWT_CRYPTOGRAPHY") from exc
    app = AccessGate(mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        max_request_body_size=65536))
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

if __name__ == "__main__":
    main()
