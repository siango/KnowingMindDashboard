"""D06 canonical question preflight over existing channel ingress.

No model inference, canon retrieval, channel send, or invented citations.
The existing D02/Dhamma source is authoritative only after a real scoped call.
"""
from __future__ import annotations

import hashlib
import json

from . import ingress
from .tools import ToolError


def preview_d06(request, ctx):
    if not isinstance(ctx, dict) or not ingress.qualified(ctx):
        raise ToolError("D06_CHANNEL_AUTH_REQUIRED")
    if (not isinstance(request, dict)
            or set(request) != {"mission_id", "task_id", "account_id", "question", "source_refs"}):
        raise ToolError("D06_REQUEST_INVALID")
    auth = ctx.get("command_auth", {})
    if (not all(isinstance(ctx.get(k), str) and ctx[k]
                and request.get(k) == ctx[k] for k in ("mission_id", "task_id", "account_id"))
            or auth.get("mission_id") != request["mission_id"]
            or auth.get("account_id") != request["account_id"]
            or request["task_id"] not in auth.get("task_ids", [])):
        raise ToolError("D06_ISSUER_SCOPE_DENIED")
    question, refs = request["question"], request["source_refs"]
    if (not isinstance(question, str) or not 1 <= len(question.strip()) <= 3500
            or not isinstance(refs, list) or len(refs) > 12
            or any(not isinstance(r, str) or not 1 <= len(r) <= 240 for r in refs)):
        raise ToolError("D06_QUESTION_OR_CITATIONS_INVALID")
    try:
        raw = json.dumps(request, sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeError):
        raise ToolError("D06_INPUT_INVALID") from None
    if len(raw) > 16000:
        raise ToolError("D06_INPUT_TOO_LARGE")
    return {"circuit": "D06", "status": "CHANNEL_QUESTION_PREFLIGHT_ONLY",
            "request_sha256": hashlib.sha256(raw).hexdigest(),
            "source_refs_supplied": len(refs),
            "source_refs_verified": False,
            "canon_retrieved": False, "model_invoked": False,
            "answer_delivered": False, "provider_receipt_verified": False,
            "authority_granted": False, "independent_qc": "NOT_RUN"}
