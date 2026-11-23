"""D07 existing receipt-memory read path; no store, correction or expiry action.

Only P08's original bounded, scoped receipt reader may perform retrieval.
Any memory write requires a separate existing owner grant/readback.
"""
from __future__ import annotations

from .kmco_continuity import run_p08
from .tools import ToolError


def read_d07(args, ctx):
    if not isinstance(args, dict) or set(args) != {"goal", "limit"}:
        raise ToolError("D07_READ_CONTRACT_INVALID")
    if (not isinstance(ctx, dict) or ctx.get("read_only") is not True
            or not all(isinstance(ctx.get(k), str) and ctx[k]
                       for k in ("mission_id", "task_id", "account_id", "workspace"))):
        raise ToolError("D07_READ_SCOPE_DENIED")
    result = run_p08(args, ctx)
    if (result.get("circuit") != "P08" or result.get("status") != "SCOPED_MEMORY_RETRIEVED_UNVERIFIED"
            or result.get("truth_owner") != "EXISTING_KMCW_RECEIPTS"
            or result.get("independent_qc") != "NOT_RUN"):
        raise ToolError("D07_MEMORY_OWNER_RESULT_INVALID")
    return {"circuit": "D07", "status": "EXISTING_RECEIPT_MEMORY_RETRIEVED_UNVERIFIED",
            "readback": result, "source_owner": "EXISTING_KMCW_RECEIPTS",
            "new_memory_saved": False, "correction_applied": False,
            "expiry_changed": False, "independent_qc": "NOT_RUN",
            "authority_granted": False}
