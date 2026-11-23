"""D08 ingress source preflight (partial): reuse existing ingress auth, no delivery.

This is deliberately NOT a transport signature verifier. Existing trusted
middleware must authenticate the incoming request before any of this code runs.
No dispatch, acknowledgement, checkpoint write, or runtime promotion.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import ingress
from .tools import ToolError

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def preflight_d08(envelope, ctx):
    """Fail closed unless issuer and payload are already bound by existing ingress.

    Returns only safe metadata, never input payload or credentials. This does
    NOT replace command ingress.execute(), where dedup and ACK reside.
    """
    if not isinstance(ctx, dict) or not ingress.qualified(ctx):
        raise ToolError("D08_EXISTING_TRANSPORT_AUTH_REQUIRED")
    if not isinstance(envelope, dict) or set(envelope) != ingress.FIELDS:
        raise ToolError("D08_SCHEMA_INVALID")
    try:
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ToolError("D08_PAYLOAD_INVALID") from None
    if len(raw) > 64000:
        raise ToolError("D08_PAYLOAD_TOO_LARGE")
    auth = ctx["command_auth"]
    principal = ctx["command_channels"][auth["channel"]]["principals"][auth["principal_id"]]
    mission, task = envelope.get("mission_id"), envelope.get("task_id")
    if (envelope.get("channel") != auth["channel"]
            or envelope.get("principal_id") != auth["principal_id"]
            or auth.get("mission_id") != mission or ctx.get("mission_id") != mission
            or auth.get("account_id") != ctx.get("account_id")
            or principal.get("account_id") != ctx.get("account_id")
            or mission not in principal.get("mission_ids", [])
            or task not in principal.get("task_ids", [])
            or task not in auth.get("task_ids", [])):
        raise ToolError("D08_ISSUER_SCOPE_DENIED")
    sha = envelope.get("source_hash")
    if not isinstance(sha, str) or not HEX40.fullmatch(sha):
        raise ToolError("D08_SOURCE_INVALID")
    if not isinstance(auth.get("body_sha256"), str) or not HEX64.fullmatch(auth["body_sha256"]):
        raise ToolError("D08_BODY_BINDING_MISSING")
    if auth["body_sha256"] != ingress.digest(envelope):
        raise ToolError("D08_BODY_BINDING_MISMATCH")
    if (not isinstance(envelope.get("scope"), dict)
            or set(envelope["scope"]) != {"plan_id", "allowed_paths", "payload"}
            or not isinstance(envelope["scope"]["payload"], dict)):
        raise ToolError("D08_SCOPE_SCHEMA_INVALID")
    try:
        key = ingress.idempotency_key(envelope)
    except (KeyError, TypeError, ValueError):
        raise ToolError("D08_IDEMPOTENCY_INPUT_INVALID") from None
    if envelope.get("idempotency_key") != key:
        raise ToolError("D08_IDEMPOTENCY_INVALID")
    return {
        "circuit": "D08",
        "status": "TRUSTED_INGRESS_PREFLIGHT_ONLY",
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "idempotency_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "payload_bytes": len(raw),
        "identity_scoped": True,
        "transport_signature_reverified": False,
        "replay_ledger_checked": False,
        "dispatched": False,
        "provider_ack_sent": False,
        "authority_granted": False,
        "independent_qc": "NOT_RUN",
    }
