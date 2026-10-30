"""V3 Phase 6: monitoring view. Reads existing state only.

Aggregates queue, leases, receipts, plan progress, QC and CI into one
status object. No scheduler, no new SSOT.
"""
from __future__ import annotations

import time
from pathlib import Path


def agent_status(ctx: dict) -> dict:
    """Build the agent status view. Never raises on missing pieces."""
    store = ctx.get("store")
    receipts_dir = ctx.get("receipts_dir")
    queue = {}
    if store is not None:
        try:
            queue = store.health()
        except Exception:
            queue = {"error": "queue unreadable"}
    receipts_count, last_receipt = 0, ""
    if receipts_dir is not None:
        try:
            files = sorted(Path(receipts_dir).glob("*.json"),
                           key=lambda p: p.stat().st_mtime)
            receipts_count = len(files)
            last_receipt = files[-1].name if files else ""
        except OSError:
            pass
    progress = ctx.get("progress", {}) or {}
    total = int(progress.get("total", 0) or 0)
    done = int(progress.get("done", 0) or 0)
    percent = round(done / total * 100, 1) if total else 0.0
    evidence = ctx.get("evidence", []) or []
    if not isinstance(evidence, list):
        evidence = []
    return {
        "MISSION": ctx.get("mission_id", "KM-INTELLIGENT-CODING-AGENT-V3"),
        "TASK": ctx.get("active_task", ""),
        "OWNER_NODE": ctx.get("owner_node", "AV15"),
        "PROGRESS_PERCENT": percent,
        "ACTIVE_MODEL": ctx.get("active_model", ""),
        "ACTIVE_TOOL": ctx.get("active_tool", ""),
        "CURRENT_STEP": ctx.get("current_step", "IDLE"),
        "QC_STATUS": ctx.get("qc_status", "NOT_RUN"),
        "RETRY_COUNT": int(ctx.get("retry_count", 0) or 0),
        "EVIDENCE": [str(e)[:200] for e in evidence][-10:],
        "NEXT_ACTION": ctx.get("next_action", "AWAIT_COMMAND"),
        "queue": queue,
        "receipts_count": receipts_count,
        "last_receipt": last_receipt,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
