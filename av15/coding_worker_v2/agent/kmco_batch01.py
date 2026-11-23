"""KMCO batch 01: canonical D08-M01..M06 and D09-M01..M04.

These stage implementations are *individually testable*. They reuse ingress,
ToolRegistry and the existing checkpoint owner. No signature, real channel ACK,
consent, durable readback or delivery is fabricated by these pure checks.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import ingress
from .kmco_d08 import preflight_d08
from .kmco_d09 import preflight_d09
from .tools import ToolError

D08 = ("verify_transport_signature", "parse_bounded_payload",
       "validate_schema_and_identity", "scrub_and_deduplicate",
       "route_to_existing_mission", "issue_ack_or_error")
D09 = ("validate_output_schema", "enforce_visibility_and_consent",
       "redact_sensitive_fields", "attach_evidence_and_provenance")
READ_COMMANDS = frozenset(("kmcw.context", "kmcw.mission.status",
                            "kmcw.task.inspect", "kmcw.evidence.read"))
SAFE_META = frozenset(("status", "summary", "result_code"))


def _serialized(value, limit):
    try:
        wire = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ToolError("KMCO_BATCH01_JSON_INVALID") from None
    if len(wire) > limit:
        raise ToolError("KMCO_BATCH01_SIZE_LIMIT")
    return wire


def d08_m01_verified_middleware(ctx):
    """Check existing ingress qualification; never claim to verify a signature."""
    if not isinstance(ctx, dict) or not ingress.qualified(ctx):
        raise ToolError("D08_M01_AUTH_REQUIRED")
    auth = ctx["command_auth"]
    # Only the trusted middleware can supply a verifiable signature. It is
    # forbidden to infer cryptographic verification from qualified=True.
    return {"operation_id": "D08-M01", "method": auth["method"],
            "existing_middleware_qualified": True,
            "transport_signature_independently_verified": False,
            "external_acceptance": "NOT_VERIFIED"}


def d08_m02_parse_bounded(payload):
    """Parse strict canonical JSON bytes; limit input *before* parsing."""
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= 64000:
        raise ToolError("D08_M02_WIRE_SIZE_OR_TYPE")
    try:
        value = json.loads(payload.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, TypeError):
        raise ToolError("D08_M02_JSON_DENIED") from None
    if not isinstance(value, dict) or set(value) != ingress.FIELDS:
        raise ToolError("D08_M02_ENVELOPE_SCHEMA")
    if _serialized(value, 64000) != payload:
        # Deterministic canonical encoding rejects duplicate keys / alternate
        # representations, avoiding confusion between auth and dispatch bodies.
        raise ToolError("D08_M02_NONCANONICAL_WIRE")
    return value


def d08_m03_validate(envelope, ctx):
    result = preflight_d08(envelope, ctx)
    return {"operation_id": "D08-M03", "payload_sha256": result["payload_sha256"],
            "identity_scoped": result["identity_scoped"],
            "authority_granted": False}


def d08_m04_dedup_read(envelope, ctx):
    """Inspect the original checkpoint reader only; never store/retry here."""
    _ = d08_m03_validate(envelope, ctx)
    store = ctx.get("store")
    if (store is None or not callable(getattr(store, "load_checkpoint", None))
            or not isinstance(getattr(store, "checkpoints", None), Path)):
        raise ToolError("D08_M04_ORIGINAL_STORE_REQUIRED")
    key = ".command-" + envelope["idempotency_key"]
    record = store.load_checkpoint(key)
    if record is None and (store.checkpoints / (key + ".json")).exists():
        raise ToolError("D08_M04_CORRUPT_CHECKPOINT")
    if record is not None:
        data = record.get("data")
        if not isinstance(data, dict) or data.get("semantic_sha256") != envelope["idempotency_key"]:
            raise ToolError("D08_M04_CONFLICT")
    return {"operation_id": "D08-M04",
            "existing_checkpoint": record is not None,
            "checkpoint_ref": "original-queue:" + key,
            "checkpoint_created": False,
            "replay_protection_enforced_by": "EXISTING_INGRESS_EXECUTE"}


def d08_m05_resolve(name, envelope, ctx, registry):
    """Bind exact command and existing tool without dispatch."""
    _ = d08_m03_validate(envelope, ctx)
    if name not in ingress.CATALOG or name not in READ_COMMANDS:
        raise ToolError("D08_M05_READ_COMMAND_REQUIRED")
    if ingress.CATALOG[name][0] != envelope["intent"]:
        raise ToolError("D08_M05_INTENT_MISMATCH")
    if name not in ctx["command_auth"].get("permissions", []):
        raise ToolError("D08_M05_PERMISSION_DENIED")
    if registry is None or name not in registry.names():
        raise ToolError("D08_M05_EXISTING_HANDLER_UNAVAILABLE")
    return {"operation_id": "D08-M05", "bound_tool": name,
            "mission_id": envelope["mission_id"], "task_id": envelope["task_id"],
            "dispatched": False, "authority_granted": False}


def d08_m06_existing_readonly(name, envelope, ctx, registry):
    """Execute through existing handler, never issue a fabricated provider ACK.

    Requires exact original ingress contract; the runtime owner is responsible
    for plan/source/TTL/ACL/checkpoints and must fail closed on any mismatch.
    """
    _ = d08_m05_resolve(name, envelope, ctx, registry)
    result = registry.call(name, {"command": envelope}, ctx)
    wire = _serialized(result, 65536)
    return {"operation_id": "D08-M06",
            "existing_handler_called": True,
            "result_sha256": hashlib.sha256(wire).hexdigest(),
            "status": "ORIGINAL_READ_COMMAND_RETURNED",
            "provider_ack_verified": False, "independent_qc": "NOT_RUN"}


def d09_m01_schema(output, ctx):
    pre = preflight_d09(output, ctx)
    return {"operation_id": "D09-M01",
            "output_sha256": pre["output_sha256"],
            "destination": pre["destination"]}


def d09_m02_acl_consent(output, ctx):
    _ = d09_m01_schema(output, ctx)
    # Only return private owner data; source consent/authorization must originate
    # from trusted middleware and cannot be inferred from output["visibility"].
    consent = ctx.get("egress_consent")
    if (not isinstance(consent, dict) or consent.get("verified") is not True
            or consent.get("mission_id") != ctx["mission_id"]
            or consent.get("task_id") != ctx["task_id"]
            or consent.get("account_id") != ctx["account_id"]
            or output["delivery_channel"] not in consent.get("channels", [])
            or not isinstance(consent.get("evidence_ref"), str)
            or not consent["evidence_ref"]):
        raise ToolError("D09_M02_CONSENT_REQUIRED")
    return {"operation_id": "D09-M02", "consent_evidence_ref": consent["evidence_ref"],
            "owner_only": True, "provider_authorization_independently_verified": False}


def d09_m03_redact(output, ctx):
    _ = d09_m02_acl_consent(output, ctx)
    # Never forward arbitrary untrusted payload keys as private output.
    # Actual user content must pass a separate authorized delivery workflow.
    payload = output["payload"]
    if not isinstance(payload, dict):
        raise ToolError("D09_M03_PAYLOAD_INVALID")
    safe = {}
    for key in SAFE_META:
        if key in payload:
            value = payload[key]
            if (not isinstance(value, str) or len(value) > 160
                    or any(ch in value for ch in ("\\n", "\\r", "\\x00"))):
                raise ToolError("D09_M03_SAFE_FIELD_INVALID")
            # A metadata field may still contain a secret; never claim text
            # is redacted. Replace its value by a hash/length only.
            safe[key] = {"sha256": hashlib.sha256(value.encode()).hexdigest(),
                         "bytes": len(value.encode())}
    return {"operation_id": "D09-M03", "sanitized_metadata": safe,
            "content_forwarded": False, "redaction_for_delivery_verified": False}


def d09_m04_evidence(output, ctx):
    _ = d09_m03_redact(output, ctx)
    trusted = ctx.get("verified_evidence_refs")
    if (not isinstance(trusted, (tuple, frozenset))
            or any(not isinstance(r, str) for r in trusted)
            or not set(output["evidence_refs"]).issubset(trusted)):
        raise ToolError("D09_M04_TRUSTED_EVIDENCE_REQUIRED")
    return {"operation_id": "D09-M04", "evidence_refs": list(output["evidence_refs"]),
            "evidence_count": len(output["evidence_refs"]),
            "output_sha256": hashlib.sha256(_serialized(output, 64000)).hexdigest(),
            "attached_to_provider": False, "durable_readback": False,
            "independent_qc": "NOT_RUN"}
