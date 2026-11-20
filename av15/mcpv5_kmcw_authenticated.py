"""Authenticated, read-only MCP V5 -> existing KMCW dispatcher.

No new queue, registry, role, token, endpoint, or runtime. The Cloudflare JWT
must verify against the exact configured subject AND match the protected
Admin V2 session email. Each call reacquires the existing Admin delegation;
only the issuer-bound tool arguments are dispatched through KMCW _call().
If either identity is unavailable, execution is denied.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from av15 import mcpv5_secure_http_readback as secure
from mcp.server.mcpserver import Context

ROOT = Path(__file__).resolve().parent.parent
mcp = secure.mcp


def _identity_match(claims, session):
    if not isinstance(claims, dict) or not isinstance(session, dict):
        return False
    email = claims.get("email")
    admin_email = session.get("email")
    if (not isinstance(email, str) or not email or
            not isinstance(admin_email, str) or not admin_email):
        return False
    return email.casefold() == admin_email.casefold() and session.get("authenticated") is True


def _runtime():
    """Existing local KMCW base and queue only; never generate a new store."""
    from argparse import Namespace
    from coding_worker_v2.agent.km_main import _ctx
    from coding_worker_v2.agent.tools import build_default_registry

    base = os.getenv("KEBUNATE_KMCW_BASE", "")
    workspace = os.getenv("KEBUNATE_KMCW_WORKSPACE", "")
    if not base or not workspace:
        raise ValueError("KMCW_EXISTING_PATHS_REQUIRED")
    base_path = Path(base).resolve(strict=True)
    workspace_path = Path(workspace).resolve(strict=True)
    if not base_path.is_dir() or not (base_path / "queue").is_dir():
        raise ValueError("KMCW_EXISTING_QUEUE_REQUIRED")
    if not workspace_path.is_dir() or not (workspace_path / ".git").exists():
        raise ValueError("KMCW_EXISTING_WORKSPACE_REQUIRED")
    ctx = _ctx(Namespace(base=str(base_path), workspace=str(workspace_path)))
    catalog_root = os.getenv("KMCO_CATALOG_ROOT", "")
    if catalog_root:
        from coding_worker_v2.agent.kmco_runtime import KMCOCatalog
        catalog = KMCOCatalog.from_snapshot(catalog_root)
        registry = build_default_registry(kmco_catalog=catalog)
    else:
        registry = build_default_registry()
    ctx["tool_registry"] = registry
    ctx["read_only"] = True
    return ctx, registry


def execute_bound_read(name, claims, runtime):
    """Use only the issuer's bound arguments; deny all client-supplied arguments."""
    from coding_worker_v2.agent.capabilities import (
        KMAdminContextProducer, bind_runtime_context, authorized,
    )
    from coding_worker_v2.agent.mcp_server import _call
    from coding_worker_v2.agent.tools import ToolError

    if not isinstance(name, str) or not name or len(name) > 120:
        raise ToolError("MCP_TOOL_NAME_INVALID")

    ctx, registry = runtime
    provider = KMAdminContextProducer.from_runtime_credentials()
    if provider is None:
        raise ToolError("KM_ADMIN_SESSION_CREDENTIAL_UNAVAILABLE")

    # Same protected cookie must identify the signed Access email.
    session = provider._read("/api/session")
    if not _identity_match(claims, session):
        raise ToolError("MCP_CLOUDFLARE_ADMIN_IDENTITY_MISMATCH")

    call_ctx = bind_runtime_context(ctx, provider)
    # Session rotation between the two Admin calls must not cross identities.
    if not _identity_match(claims, provider._read("/api/session")):
        raise ToolError("MCP_CLOUDFLARE_ADMIN_IDENTITY_MISMATCH")
    auth = call_ctx.get("command_auth", {})
    if name not in auth.get("permissions", []):
        raise ToolError("MCP_TOOL_SCOPE_DENIED")
    contracts = {row["name"]: row for row in registry.command_contract()["commands"]}
    spec = contracts.get(name)
    if not spec or spec["permission"] != "read" or name.startswith("db."):
        raise ToolError("MCP_READ_ONLY_TOOL_REQUIRED")
    if not authorized(spec, call_ctx):
        raise ToolError("MCP_TOOL_SCOPE_DENIED")

    bindings = call_ctx.get("tool_bindings", {})
    binding = bindings.get(name)
    if not isinstance(binding, dict) or not isinstance(binding.get("arguments"), dict):
        raise ToolError("MCP_ISSUER_ARGUMENT_BINDING_MISSING")

    call_ctx["read_only"] = True
    result = _call(registry, {"name": name, "arguments": binding["arguments"]}, call_ctx)
    # _call owns the existing queue audit. This is not an independent receipt.
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "ok": True, "tool": name, "result": result,
        "result_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "verified_receipt": False,
        "independent_qc": "NOT_RUN",
        "execution_authority": False,
    }


@mcp.tool()
async def kmcw_authorized_read(tool_name: str, ctx: Context) -> dict:
    """Call one issuer-bound read tool using SDK request-scoped HTTP headers."""
    from coding_worker_v2.agent.tools import ToolError
    try:
        # MCP SDK v2 injects per-request Context. Do not rely on a ContextVar
        # propagated from ASGI middleware to the SDK's independent task group.
        headers = ctx.headers or {}
        assertion = headers.get("cf-access-jwt-assertion", "")
        auth = headers.get("authorization", "")
        if not assertion and auth.startswith("Bearer "):
            assertion = auth[7:]
        claims = secure.validate_access_jwt(assertion)
        runtime = _runtime()
        return execute_bound_read(tool_name, claims, runtime)
    except (ToolError, ValueError, OSError) as exc:
        reason = exc.reason if isinstance(exc, ToolError) else type(exc).__name__
        return {"ok": False, "reason": reason, "execution_authority": False}


class IdentityGate(secure.AccessGate):
    """Carry only server-verified claims into the current MCP HTTP request."""
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("path") != "/mcp":
            return await self._reject(send, 404)
        headers = scope.get("headers", [])
        # Detect duplicate security headers, not only differing values.
        names = [k.lower() for k, _ in headers]
        if any(names.count(key) > 1 for key in
               (b"authorization", b"cf-access-jwt-assertion")):
            return await self._reject(send, 401)
        hdr = {k.lower(): v for k, v in headers}
        assertion = hdr.get(b"cf-access-jwt-assertion", b"")
        authorization = hdr.get(b"authorization", b"")
        # Managed OAuth may use a different Authorization bearer value.
        # Prefer Cloudflare's signed Access assertion if provided.
        if not assertion and authorization.startswith(b"Bearer "):
            assertion = authorization[7:]
        try:
            secure.validate_access_jwt(assertion.decode("ascii"))
        except Exception:
            return await self._reject(send, 401)
        return await self.app(scope, receive, send)


def main():
    if os.getenv("KEBUNATE_HTTP_KMCW_CONFIRM") != "YES":
        raise SystemExit("BLOCKED: EXPLICIT_KMCW_HTTP_CONFIRM_REQUIRED")
    if not os.getenv("KEBUNATE_ACCESS_SUB"):
        raise SystemExit("BLOCKED: EXACT_ALLOWED_ACCESS_SUB_REQUIRED")
    if not os.getenv("CREDENTIALS_DIRECTORY"):
        raise SystemExit("BLOCKED: ADMIN_CREDENTIAL_CHANNEL_REQUIRED")
    # Verify the existing paths before exposing a listener.
    _runtime()
    import uvicorn
    app = IdentityGate(mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True,
        json_response=True, max_request_body_size=65536))
    uvicorn.run(app, host=secure.HOST, port=secure.PORT, log_level="warning")


if __name__ == "__main__":
    main()
