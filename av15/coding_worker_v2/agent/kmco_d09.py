"""D09 output egress preflight: provenance and ACL checks, no provider send.

Read-only projection only. Not a replacement for channel/provider delivery
receipts. It never returns message payload, credentials or personal data.
"""
from __future__ import annotations

import hashlib
import json
import re

from .tools import ToolError

REF = re.compile(r"^[A-Za-z0-9_./:#-]{1,240}$")
FIELDS = {"mission_id", "task_id", "account_id", "visibility",
          "evidence_refs", "payload", "delivery_channel"}


def preflight_d09(output, ctx):
    if not isinstance(ctx, dict) or not isinstance(output, dict) or set(output) != FIELDS:
        raise ToolError("D09_OUTPUT_SCHEMA_DENIED")
    if (not isinstance(ctx.get("mission_id"), str) or not ctx["mission_id"]
            or not isinstance(ctx.get("task_id"), str) or not ctx["task_id"]
            or not isinstance(ctx.get("account_id"), str) or not ctx["account_id"]
            or any(output[k] != ctx[k] for k in ("mission_id", "task_id", "account_id"))):
        raise ToolError("D09_ISSUER_SCOPE_DENIED")
    if (output["visibility"] != "PRIVATE_OWNER"
            or ctx.get("read_only") is not True
            or ctx.get("egress_owner_authenticated") is not True):
        raise ToolError("D09_VISIBILITY_OR_AUTH_DENIED")
    refs = output["evidence_refs"]
    if (not isinstance(refs, list) or not 1 <= len(refs) <= 20
            or any(not isinstance(ref, str) or not REF.fullmatch(ref) for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ToolError("D09_EVIDENCE_INVALID")
    if (output["delivery_channel"] not in ("CHATGPT", "ADMIN_V2", "LINE", "GITHUB")
            or not isinstance(output["payload"], dict)):
        raise ToolError("D09_DESTINATION_OR_PAYLOAD_INVALID")
    try:
        raw = json.dumps(output, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ToolError("D09_PAYLOAD_INVALID") from None
    if len(raw) > 64000:
        raise ToolError("D09_PAYLOAD_TOO_LARGE")
    # Never infer payload safety from keyword redaction: no raw payload returned.
    return {"circuit": "D09", "status": "PRIVATE_OWNER_EGRESS_PREFLIGHT_ONLY",
            "output_sha256": hashlib.sha256(raw).hexdigest(),
            "evidence_count": len(refs), "destination": output["delivery_channel"],
            "payload_returned": False, "payload_delivered": False,
            "provider_receipt_verified": False, "authority_granted": False,
            "independent_qc": "NOT_RUN"}
