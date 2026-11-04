"""Phase 2/5: evidence envelopes + durable local receipts.

Receipts are the offline-safe record. A latest-receipt pointer is also
written locally; central SSOT transport is not verified by this module.
"""
from __future__ import annotations

import json
import hashlib
import os
import fcntl
import time
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
    hints = task.task_input.hints or {}
    body = {
        "schema": SCHEMA,
        "mission_id": task.mission_id,
        "task_id": task.task_id,
        "owner_node": task.owner_node,
        "source_sha": hints.get("base_commit", ""),
        "scope_digest": hints.get("scope_digest", ""),
        "lease_token": task.lease_token,
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
    history_dir = receipts_dir / "history" / task.task_id
    history_dir.mkdir(parents=True, exist_ok=True)
    lock_path = receipts_dir / ".receipt.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        versions = sorted(history_dir.glob("v*.json"))
        version = 1
        previous_digest = ""
        if versions:
            latest = versions[-1]
            try:
                version = int(latest.stem[1:]) + 1
                previous_digest = hashlib.sha256(
                    latest.read_bytes()).hexdigest()
            except (OSError, ValueError):
                raise ValueError("receipt history is corrupt") from None
        receipt = build_receipt(task, extra)
        receipt["receipt_version"] = version
        receipt["previous_receipt_sha256"] = previous_digest
        body = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        path = history_dir / f"v{version:06d}.json"
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            raise ValueError("receipt version collision") from None

        latest_path = receipts_dir / f"{task.task_id}.json"
        tmp = latest_path.with_suffix(f".{time.time_ns()}.tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(latest_path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
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
