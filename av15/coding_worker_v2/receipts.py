"""Phase 2/5: evidence envelopes + durable local receipts.

Receipts are the offline-safe record. The latest receipt pointer mirrors
into av15/state/ so the existing reconcile.sh flow can announce it via
the P02 bridge without any new control plane or competing SSOT.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .providers import redact
from .tasks import Task

SCHEMA = "KM_CODING_RECEIPT_V1"


def _scrub(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(k)[:120]: _scrub(v) for k, v in list(value.items())[:200]}
    if isinstance(value, list):
        return [_scrub(v) for v in value[:200]]
    return value


def build_receipt(task: Task, extra: dict | None = None) -> dict:
    body = {
        "schema": SCHEMA,
        "mission_id": task.mission_id,
        "task_id": task.task_id,
        "owner_node": task.owner_node,
        "input": task.to_dict()["input"],
        "output": task.output,
        "status": task.status,
        "evidence": task.evidence,
        "next_action": task.next_action,
        "failure_class": task.failure_class,
        "attempts": task.attempts,
    }
    if extra:
        body["extra"] = extra
    return _scrub(body)


def write_receipt(receipts_dir: Path, task: Task, extra: dict | None = None) -> Path:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt = build_receipt(task, extra)
    path = receipts_dir / f"{task.task_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    # Latest-pointer for existing AV15 state/reconcile pickup.
    # Overridable so unit tests never touch real AV15 state.
    try:
        state_dir = Path(os.environ.get("KMW2_STATE_DIR", "") or
                         Path(__file__).resolve().parent.parent / "state")
        state_dir.mkdir(parents=True, exist_ok=True)
        pointer = state_dir / "coding_worker_v2_last.json"
        ptmp = pointer.with_suffix(".tmp")
        ptmp.write_text(json.dumps({
            "schema": SCHEMA,
            "task_id": task.task_id,
            "mission_id": task.mission_id,
            "status": task.status,
            "receipt": str(path),
        }, ensure_ascii=False), encoding="utf-8")
        ptmp.replace(pointer)
    except OSError:
        pass
    return path


def read_receipt(path: Path) -> dict:
    """SSOT receipt readback (local leg). Raises on invalid receipt."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("not a KM_CODING_RECEIPT_V1 receipt")
    for key in ("mission_id", "task_id", "owner_node", "input", "output",
                "status", "evidence", "next_action"):
        if key not in data:
            raise ValueError(f"receipt missing {key}")
    return data
