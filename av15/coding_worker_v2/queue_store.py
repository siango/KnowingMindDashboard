"""Phase 3: persistent queue, idempotency, lease + heartbeat, recovery.

Layout under base dir (default av15/coding_worker_v2/queue):
  inbox/<task_id>.json        queued tasks
  doing/<task_id>.json        claimed tasks
  doing/<task_id>.lease.json  lease + heartbeat state
  done/<task_id>/task.json    completed envelopes
  failed/<task_id>/task.json  exhausted envelopes (+requeue via inbox)
  index/idem.json             idempotency_key -> task_id
  checkpoints/<task_id>.json  resume state

A worker never duplicates an active task and never continues an expired
lease without reconciliation (expired leases requeue).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .tasks import Task, admit

LEASE_TTL_S = 300
HEARTBEAT_EVERY_S = 60


class QueueStore:
    def __init__(self, base: Path):
        self.base = base
        self.inbox = base / "inbox"
        self.doing = base / "doing"
        self.done = base / "done"
        self.failed = base / "failed"
        self.index = base / "index"
        self.checkpoints = base / "checkpoints"
        for path in (self.inbox, self.doing, self.done, self.failed,
                     self.index, self.checkpoints):
            path.mkdir(parents=True, exist_ok=True)
        self.idem_file = self.index / "idem.json"

    # -- persistence helpers ------------------------------------------------
    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _read_idem(self) -> dict:
        try:
            data = json.loads(self.idem_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # -- enqueue (idempotent) -----------------------------------------------
    def enqueue(self, task: Task) -> tuple[str, bool]:
        """Returns (task_id, created). Same key returns existing task."""
        admit(task)
        idem = self._read_idem()
        if task.idempotency_key in idem:
            return str(idem[task.idempotency_key]), False
        for existing in list(self.inbox.glob("*.json")) + list(self.doing.glob("*.json")):
            if existing.name.endswith(".lease.json"):
                continue
            try:
                other = json.loads(existing.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(other, dict) and other.get("idempotency_key") == task.idempotency_key:
                idem[task.idempotency_key] = other.get("task_id", existing.stem)
                self._write_json(self.idem_file, idem)
                return str(idem[task.idempotency_key]), False
        task.status = "ADMITTED" if task.status == "QUEUED" else task.status
        self._write_json(self.inbox / f"{task.task_id}.json", task.to_dict())
        idem[task.idempotency_key] = task.task_id
        self._write_json(self.idem_file, idem)
        return task.task_id, True

    # -- claim + lease --------------------------------------------------------
    def claim(self) -> Task | None:
        """Claim oldest inbox task into doing/ with a fresh lease."""
        candidates = sorted(self.inbox.glob("*.json"))
        if not candidates:
            return None
        path = candidates[0]
        try:
            task = Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            path.rename(self.failed / path.name)
            return None
        task.attempts += 1
        task.status = "RUNNING"
        self._write_json(self.doing / path.name, task.to_dict())
        path.unlink(missing_ok=True)
        now = time.time()
        self._write_json(self.doing / f"{task.task_id}.lease.json", {
            "task_id": task.task_id, "owner_pid": os.getpid(),
            "acquired_at": now, "heartbeat_at": now, "expires_at": now + LEASE_TTL_S,
        })
        return task

    def lease_valid(self, task_id: str) -> bool:
        try:
            lease = json.loads((self.doing / f"{task_id}.lease.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return float(lease.get("expires_at", 0)) > time.time()

    def heartbeat(self, task_id: str) -> bool:
        """Renew lease. Returns False when lease is gone/expired (must stop)."""
        path = self.doing / f"{task_id}.lease.json"
        try:
            lease = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if float(lease.get("expires_at", 0)) <= time.time():
            return False
        now = time.time()
        lease["heartbeat_at"] = now
        lease["expires_at"] = now + LEASE_TTL_S
        self._write_json(path, lease)
        return True

    # -- completion -----------------------------------------------------------
    def complete(self, task: Task) -> None:
        task.status = "DONE"
        dest = self.done / task.task_id
        dest.mkdir(parents=True, exist_ok=True)
        self._write_json(dest / "task.json", task.to_dict())
        (self.doing / f"{task.task_id}.json").unlink(missing_ok=True)
        (self.doing / f"{task.task_id}.lease.json").unlink(missing_ok=True)
        (self.checkpoints / f"{task.task_id}.json").unlink(missing_ok=True)

    def fail(self, task: Task, failure_class: str, next_action: str) -> str:
        """Requeue with backoff count or exile to failed/. Returns 'requeued'|'failed'."""
        task.failure_class = failure_class
        task.next_action = next_action
        (self.doing / f"{task.task_id}.lease.json").unlink(missing_ok=True)
        if task.attempts < task.max_attempts:
            task.status = "ADMITTED"
            self._write_json(self.inbox / f"{task.task_id}.json", task.to_dict())
            (self.doing / f"{task.task_id}.json").unlink(missing_ok=True)
            return "requeued"
        task.status = "FAILED"
        dest = self.failed / task.task_id
        dest.mkdir(parents=True, exist_ok=True)
        self._write_json(dest / "task.json", task.to_dict())
        (self.doing / f"{task.task_id}.json").unlink(missing_ok=True)
        return "failed"

    # -- crash / restart recovery ----------------------------------------------
    def recover(self) -> dict:
        """Requeue doing/ entries with expired or missing leases."""
        recovered, kept = [], []
        for path in sorted(self.doing.glob("*.json")):
            if path.name.endswith(".lease.json"):
                continue
            task_id = path.stem
            if self.lease_valid(task_id):
                kept.append(task_id)
                continue
            try:
                task = Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                path.rename(self.failed / path.name)
                (self.doing / f"{task_id}.lease.json").unlink(missing_ok=True)
                continue
            task.status = "ADMITTED"
            task.next_action = f"RECOVERED_AFTER_RESTART attempts={task.attempts}"
            task.evidence.append({"event": "lease_reconciled", "lease_valid": False})
            self._write_json(self.inbox / path.name, task.to_dict())
            path.unlink(missing_ok=True)
            (self.doing / f"{task_id}.lease.json").unlink(missing_ok=True)
            recovered.append(task_id)
        return {"recovered": recovered, "kept_active": kept}

    # -- checkpoints ------------------------------------------------------------
    def save_checkpoint(self, task_id: str, stage: str, data: dict) -> None:
        self._write_json(self.checkpoints / f"{task_id}.json",
                         {"task_id": task_id, "stage": stage, "data": data})

    def load_checkpoint(self, task_id: str) -> dict | None:
        try:
            data = json.loads((self.checkpoints / f"{task_id}.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    # -- health -----------------------------------------------------------------
    def health(self) -> dict:
        def count(path: Path, pattern: str) -> int:
            return sum(1 for _ in path.glob(pattern))
        return {
            "inbox": count(self.inbox, "*.json"),
            "doing": sum(1 for p in self.doing.glob("*.json")
                         if not p.name.endswith(".lease.json")),
            "done": sum(1 for p in self.done.iterdir()),
            "failed": sum(1 for p in self.failed.iterdir()),
        }
