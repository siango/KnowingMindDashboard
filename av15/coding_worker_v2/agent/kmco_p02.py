"""KMCO P02: bounded, deterministic mission-context continuity.

Implements P02-M01..M05. Operates on real caller-provided task snapshots,
does not issue new credentials, change authority, or invoke model/handler.
"""
from __future__ import annotations

import hashlib
import json
import re

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class P02Blocked(ValueError):
    pass


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise P02Blocked("P02_CONTEXT_INVALID") from None


def _check_context(ctx):
    if not isinstance(ctx, dict) or set(ctx) != {
        "mission_id", "task_id", "scope", "constraints", "source_sha"
    }:
        raise P02Blocked("P02_CONTEXT_INVALID")
    for field in ("mission_id", "task_id"):
        value = ctx[field]
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise P02Blocked("P02_ID_INVALID")
    if (not isinstance(ctx["scope"], list) or not ctx["scope"]
            or any(not isinstance(s, str) or not s.strip() or len(s) > 240
                   for s in ctx["scope"])
            or len(ctx["scope"]) != len(set(ctx["scope"]))
            or not isinstance(ctx["constraints"], list)
            or any(not isinstance(s, str) or not s.strip() or len(s) > 240
                   for s in ctx["constraints"])
            or len(ctx["constraints"]) != len(set(ctx["constraints"]))
            or not isinstance(ctx["source_sha"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", ctx["source_sha"])):
        raise P02Blocked("P02_SCOPE_OR_SHA_INVALID")
    if len(_canonical(ctx)) > 16384:
        raise P02Blocked("P02_CONTEXT_TOO_LARGE")
    return ctx


def mission_anchor(ctx):
    """P02-M01: validate exact mission/task/scope, never infer identity."""
    ctx = _check_context(ctx)
    return {"mission_id": ctx["mission_id"], "task_id": ctx["task_id"],
            "scope": list(ctx["scope"]), "source_sha": ctx["source_sha"]}


def context_fingerprint(ctx):
    """P02-M02: stable digest over complete, validated task snapshot."""
    _check_context(ctx)
    return hashlib.sha256(_canonical(ctx)).hexdigest()


def context_diff(previous, current):
    """P02-M03: detect changes; reject identity or source revisions."""
    mission_anchor(previous)
    mission_anchor(current)
    if any(previous[k] != current[k] for k in ("mission_id", "task_id", "source_sha")):
        raise P02Blocked("P02_IDENTITY_OR_SOURCE_CHANGED")
    return {
        "before_sha256": context_fingerprint(previous),
        "after_sha256": context_fingerprint(current),
        "changed_fields": [key for key in ("scope", "constraints")
                           if previous[key] != current[key]],
    }


def constraint_pin(previous, current):
    """P02-M04: reject dropping previous constraints/scope on resume."""
    diff = context_diff(previous, current)
    if not set(previous["scope"]).issubset(set(current["scope"])):
        raise P02Blocked("P02_SCOPE_LOSS")
    if not set(previous["constraints"]).issubset(set(current["constraints"])):
        raise P02Blocked("P02_CONSTRAINT_LOSS")
    return {"preserved": True, "diff": diff,
            "constraints": list(current["constraints"])}


def resume_packet(previous, current):
    """P02-M05: return proposed packet only; no write/authority promotion."""
    pin = constraint_pin(previous, current)
    return {"circuit": "P02", "status": "RESUME_PACKET_PREPARED",
            "mission_id": current["mission_id"], "task_id": current["task_id"],
            "source_sha": current["source_sha"],
            "context_sha256": pin["diff"]["after_sha256"],
            "previous_sha256": pin["diff"]["before_sha256"],
            "scope": list(current["scope"]),
            "constraints": pin["constraints"],
            "changed_fields": pin["diff"]["changed_fields"],
            "authority_granted": False, "execution_done": False,
            "receipt_verified": False}


def run_p02(previous, current):
    """Execute deterministic five-step validation of supplied real snapshots."""
    mission_anchor(previous)
    mission_anchor(current)
    context_fingerprint(current)
    context_diff(previous, current)
    constraint_pin(previous, current)
    result = resume_packet(previous, current)
    result["steps_completed"] = [
        "P02-M01", "P02-M02", "P02-M03", "P02-M04", "P02-M05"
    ]
    return result
