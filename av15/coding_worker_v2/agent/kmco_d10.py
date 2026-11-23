"""D10 compare supplied versioned snapshots without writing a projection.

The input is not an authenticated database readback. Only a source-owned D02
read or a verified ledger can establish freshness; no cursor/watermark is forged.
"""
from __future__ import annotations
import hashlib
import json
import re

from .tools import ToolError

_SHA = re.compile(r"^[0-9a-f]{40}$")


def preview_d10(source, projection, ctx):
    if (not isinstance(ctx, dict) or ctx.get("read_only") is not True
            or not all(isinstance(ctx.get(k), str) and ctx[k]
                       for k in ("mission_id", "task_id", "account_id", "source_sha"))
            or not isinstance(source, dict) or not isinstance(projection, dict)):
        raise ToolError("D10_TRUSTED_READ_SCOPE_REQUIRED")
    required={"mission_id", "task_id", "account_id", "source_sha",
              "dataset", "revision", "data_sha256", "evidence_ref"}
    for item in (source, projection):
        if set(item) != required:
            raise ToolError("D10_SNAPSHOT_SCHEMA_INVALID")
        if (any(item[k] != ctx[k] for k in ("mission_id", "task_id", "account_id", "source_sha"))
                or not isinstance(item["dataset"], str) or not 1 <= len(item["dataset"]) <= 160
                or not isinstance(item["revision"], str) or not 1 <= len(item["revision"]) <= 160
                or not isinstance(item["evidence_ref"], str) or not item["evidence_ref"]
                or not isinstance(item["data_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["data_sha256"])):
            raise ToolError("D10_SNAPSHOT_PROVENANCE_INVALID")
    if source["dataset"] != projection["dataset"]:
        raise ToolError("D10_CROSS_DATASET_DENIED")
    try:
        raw=json.dumps([source,projection],sort_keys=True,ensure_ascii=False,
                       separators=(",",":"),allow_nan=False).encode()
    except (TypeError,ValueError,UnicodeError):
        raise ToolError("D10_SNAPSHOT_INVALID") from None
    if len(raw)>16384:
        raise ToolError("D10_SNAPSHOT_TOO_LARGE")
    drift=(source["revision"] != projection["revision"] or
           source["data_sha256"] != projection["data_sha256"])
    return {"circuit":"D10","status":"SUPPLIED_VERSIONS_COMPARED_UNVERIFIED",
            "dataset":source["dataset"],"drift_observed":drift,
            "evidence_pair_sha256":hashlib.sha256(raw).hexdigest(),
            "source_live_read":False,"projection_written":False,
            "conflict_reconciled":False,"authority_granted":False,
            "independent_qc":"NOT_RUN"}
