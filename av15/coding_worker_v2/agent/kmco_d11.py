"""D11 observational triage against caller-supplied existing ledger evidence.

Not a live monitor, quota reader, alert sender or recovery executor.
Never infers successful delivery/verification from a provided observation.
"""
from __future__ import annotations

import hashlib
import json
import re

from .tools import ToolError

_ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")
_FIELDS = {"mission_id", "task_id", "source_sha", "observations"}
_OBS = {"source", "evidence_ref", "state", "error_code", "quota_remaining",
        "trace_id"}
_STATES = {"HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"}


def inspect_d11(request, ctx):
    if not isinstance(ctx, dict) or not isinstance(request, dict) or set(request) != _FIELDS:
        raise ToolError("D11_INPUT_INVALID")
    if (not ctx.get("read_only") is True
            or not all(isinstance(ctx.get(k), str) and ctx[k]
                       and request.get(k) == ctx[k] for k in ("mission_id", "task_id"))
            or not isinstance(ctx.get("source_sha"), str)
            or request["source_sha"] != ctx["source_sha"]
            or not re.fullmatch(r"[0-9a-f]{40}", ctx["source_sha"])):
        raise ToolError("D11_SOURCE_SCOPE_DENIED")
    observations = request["observations"]
    if not isinstance(observations, list) or not 1 <= len(observations) <= 24:
        raise ToolError("D11_OBSERVATIONS_INVALID")
    try:
        raw = json.dumps(request, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError):
        raise ToolError("D11_PAYLOAD_INVALID") from None
    if len(raw) > 65536:
        raise ToolError("D11_PAYLOAD_TOO_LARGE")
    results = []
    for obs in observations:
        if (not isinstance(obs, dict) or set(obs) != _OBS
                or not all(isinstance(obs[k], str) and _ID.fullmatch(obs[k])
                           for k in ("source", "evidence_ref", "trace_id"))
                or not isinstance(obs["state"], str) or obs["state"] not in _STATES
                or not isinstance(obs["error_code"], str)
                or len(obs["error_code"]) > 120
                or (obs["error_code"] and not _ID.fullmatch(obs["error_code"]))
                or (obs["quota_remaining"] is not None
                    and (type(obs["quota_remaining"]) is not int
                         or obs["quota_remaining"] < 0))):
            raise ToolError("D11_OBSERVATION_SCHEMA_DENIED")
        results.append({"source": obs["source"], "state": obs["state"],
                        "trace_id": obs["trace_id"],
                        "evidence_ref": obs["evidence_ref"],
                        "error_fingerprint": (hashlib.sha256(obs["error_code"].encode()).hexdigest()
                                              if obs["error_code"] else None),
                        "quota_remaining_observed": obs["quota_remaining"]})
    return {
        "circuit": "D11",
        "status": "SUPPLIED_OBSERVATIONS_CLASSIFIED_UNVERIFIED",
        "observations": results,
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "read_live_ledger": False,
        "alert_sent": False,
        "recovery_executed": False,
        "independent_qc": "NOT_RUN",
        "authority_granted": False,
    }
