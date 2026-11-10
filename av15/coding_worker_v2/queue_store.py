"""Phase 3: persistent queue, idempotency, lease + heartbeat, recovery.

Layout under base dir (default av15/coding_worker_v2/queue):
  inbox/<task_id>.json        queued tasks
  doing/<task_id>.json        claimed tasks
  doing/<task_id>.lease.json  lease + heartbeat state
  done/<task_id>/task.json    completed envelopes
  failed/<task_id>/task.json  exhausted envelopes (+requeue via inbox)
  index/idem.json             idempotency_key -> task_id
  checkpoints/<task_id>.json  resume state
  events/<task_id>.jsonl      append-only, hash-chained, idempotent execution events

A worker never duplicates an active task and never continues an expired
lease without reconciliation (expired leases requeue).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .tasks import Task, admit, ID_RE

LEASE_TTL_S = 300
HEARTBEAT_EVERY_S = 60
EVENT_CONTRACT = "KMCW_EVENT_V1"
EVENT_FIELDS = ("account_id", "mission_id", "execution_id", "command_id",
                "parent_command_id", "prompt_version", "owner_node", "tool", "skill",
                "provider", "model", "source_sha", "receipt_uri", "receipt_sha256",
                "provenance", "usage", "usage_source", "provider_request_id",
                "detail", "next_action", "difficulty", "learning", "gate")


def _scrub_event(value, depth=0):
    from .providers import redact
    if depth > 6:
        return None
    if isinstance(value, str):
        return redact(value)[:2000]
    if isinstance(value, dict):
        return {str(k)[:80]: _scrub_event(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple)):
        return [_scrub_event(v, depth + 1) for v in list(value)[:60]]
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:200]


class QueueLeaseError(RuntimeError):
    pass


class QueueStore:
    def __init__(self, base: Path):
        self.base = base
        self.inbox = base / "inbox"
        self.doing = base / "doing"
        self.done = base / "done"
        self.failed = base / "failed"
        self.index = base / "index"
        self.checkpoints = base / "checkpoints"
        self.events = base / "events"
        for path in (self.inbox, self.doing, self.done, self.failed,
                     self.index, self.checkpoints, self.events):
            path.mkdir(parents=True, exist_ok=True)
        self.idem_file = self.index / "idem.json"
        self.lock_file = base / ".queue.lock"

    @contextmanager
    def _locked(self):
        import fcntl

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_file.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def keepalive(self, task: Task):
        stop = threading.Event()
        lost = threading.Event()
        interval = max(0.01, min(HEARTBEAT_EVERY_S, LEASE_TTL_S / 3))

        def renew() -> None:
            while not stop.wait(interval):
                try:
                    if not self.heartbeat(task.task_id, task.lease_token):
                        lost.set()
                        return
                except QueueLeaseError:
                    lost.set()
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield lost
        finally:
            stop.set()
            thread.join(timeout=max(1, interval * 2))

    def _assert_lease(self, task_id: str, lease_token: str) -> dict:
        if not lease_token:
            raise QueueLeaseError(f"missing lease token for {task_id}")
        try:
            lease = json.loads(
                (self.doing / f"{task_id}.lease.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise QueueLeaseError(f"lease unavailable for {task_id}") from None
        if lease.get("lease_token") != lease_token:
            raise QueueLeaseError(f"stale lease token for {task_id}")
        if float(lease.get("expires_at", 0)) <= time.time():
            raise QueueLeaseError(f"lease expired for {task_id}")
        return lease

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
    def _task_event(self, task: Task, step: str, status: str, **extra) -> dict:
        hints = task.task_input.hints or {}
        execution_id = f"{task.task_id}:a{task.attempts}"
        return self.append_event(
            task.task_id, step, status,
            hashlib.sha256(f"{execution_id}|{step}|{status}".encode()).hexdigest(),
            account_id=hints.get("account_id"), mission_id=task.mission_id,
            execution_id=execution_id, command_id=hints.get("plan_id"),
            parent_command_id=hints.get("parent_command_id"),
            owner_node=task.owner_node, source_sha=hints.get("base_commit"),
            provenance="QueueStore", **extra)

    def enqueue(self, task: Task) -> tuple[str, bool]:
        """Returns (task_id, created). Same key returns existing task."""
        admit(task)
        with self._locked():
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
        self._task_event(task, "queue.enqueue", "ADMITTED")
        return task.task_id, True

    # -- claim + lease --------------------------------------------------------
    def claim(self, task_id: str = "") -> Task | None:
        """Claim oldest inbox task into doing/ with a fresh lease."""
        with self._locked():
            if task_id and not ID_RE.fullmatch(task_id):
                raise ValueError("invalid task id")
            candidates = ([self.inbox / f"{task_id}.json"] if task_id else
                          sorted(self.inbox.glob("*.json")))
            for path in candidates:
                if not path.is_file():
                    continue
                checkpoint = self.load_checkpoint(path.stem) or {}
                if checkpoint.get("stage") == "waiting_approval":
                    continue
                if checkpoint.get("stage") == "waiting_provider":
                    retry_at = checkpoint.get("data", {}).get("retry_at")
                    if retry_at is None or time.time() < retry_at:
                        continue
                try:
                    task = Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    path.rename(self.failed / path.name)
                    continue
                destination = self.doing / path.name
                if destination.exists():
                    continue
                task.attempts += 1
                task.status = "RUNNING"
                token = uuid.uuid4().hex
                now = time.time()
                self._write_json(self.doing / f"{task.task_id}.lease.json", {
                    "task_id": task.task_id, "owner_pid": os.getpid(),
                    "lease_token": token, "acquired_at": now,
                    "heartbeat_at": now, "expires_at": now + LEASE_TTL_S,
                })
                try:
                    os.rename(path, destination)
                except OSError:
                    (self.doing / f"{task.task_id}.lease.json").unlink(missing_ok=True)
                    continue
                self._write_json(destination, task.to_dict())
                task.lease_token = token
                break
            else:
                return None
        self._task_event(task, "queue.claim", "CLAIMED")
        return task

    def lease_valid(self, task_id: str, lease_token: str = "") -> bool:
        try:
            lease = json.loads((self.doing / f"{task_id}.lease.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (float(lease.get("expires_at", 0)) > time.time()
                and (not lease_token or lease.get("lease_token") == lease_token))

    def heartbeat(self, task_id: str, lease_token: str = "") -> bool:
        """Renew lease. Returns False when lease is gone/expired (must stop)."""
        with self._locked():
            if not lease_token:
                return False
            try:
                lease = self._assert_lease(task_id, lease_token)
            except QueueLeaseError:
                return False
            now = time.time()
            lease["heartbeat_at"] = now
            lease["expires_at"] = now + LEASE_TTL_S
            self._write_json(self.doing / f"{task_id}.lease.json", lease)
            return True

    # -- completion -----------------------------------------------------------
    @staticmethod
    def _terminal_record(task: Task) -> dict:
        record = task.to_dict()
        if task.lease_token:
            record["lease_token_sha256"] = hashlib.sha256(
                task.lease_token.encode("utf-8")).hexdigest()
        return record

    def complete(self, task: Task) -> None:
        with self._locked():
            self._assert_lease(task.task_id, task.lease_token)
            task.status = "DONE"
            dest = self.done / task.task_id
            dest.mkdir(parents=True, exist_ok=True)
            self._write_json(dest / "task.json", self._terminal_record(task))
            (self.doing / f"{task.task_id}.json").unlink(missing_ok=True)
            (self.doing / f"{task.task_id}.lease.json").unlink(missing_ok=True)
            (self.checkpoints / f"{task.task_id}.json").unlink(missing_ok=True)
        self._task_event(task, "queue.complete", "DONE")

    def fail(self, task: Task, failure_class: str, next_action: str,
             retry: bool = True) -> str:
        """Requeue with backoff count or exile to failed/. Returns 'requeued'|'failed'."""
        with self._locked():
            self._assert_lease(task.task_id, task.lease_token)
            task.failure_class = failure_class
            task.next_action = next_action
            (self.doing / f"{task.task_id}.lease.json").unlink(missing_ok=True)
            if retry and task.attempts < task.max_attempts:
                task.status = "ADMITTED"
                self._write_json(self.inbox / f"{task.task_id}.json", task.to_dict())
                (self.doing / f"{task.task_id}.json").unlink(missing_ok=True)
                outcome = "requeued"
            else:
                task.status = "FAILED"
                dest = self.failed / task.task_id
                dest.mkdir(parents=True, exist_ok=True)
                self._write_json(dest / "task.json", self._terminal_record(task))
                (self.doing / f"{task.task_id}.json").unlink(missing_ok=True)
                outcome = "failed"
        self._task_event(task, "queue.fail", outcome.upper(), detail=failure_class,
                         next_action=next_action)
        return outcome

    def defer(self, task: Task) -> None:
        """Release a live lease without retrying a blocked provider in a loop."""
        with self._locked():
            self._assert_lease(task.task_id, task.lease_token)
            task.status = "BLOCKED"
            self._write_json(self.inbox / f"{task.task_id}.json", task.to_dict())
            (self.doing / f"{task.task_id}.json").unlink(missing_ok=True)
            (self.doing / f"{task.task_id}.lease.json").unlink(missing_ok=True)
        self._task_event(task, "queue.defer", "BLOCKED", next_action=task.next_action)

    # -- crash / restart recovery ----------------------------------------------
    def recover(self) -> dict:
        """Requeue doing/ entries with expired or missing leases."""
        with self._locked():
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
                recovered.append(task)
        for task in recovered:
            self._task_event(task, "queue.recover", "REQUEUED_AFTER_LEASE_EXPIRY")
        return {"recovered": [task.task_id for task in recovered], "kept_active": kept}

    # -- append-only execution events ------------------------------------------
    @staticmethod
    def _event_hash(event: dict) -> str:
        body = {k: v for k, v in event.items() if k != "event_hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()

    def append_event(self, task_id: str, step: str, status: str,
                     idempotency_key: str = "", **fields) -> dict:
        """Durable, redacted, hash-chained event. Same idempotency key = one event.

        Delivery may repeat (retry/resume/replay); accounting never does.
        """
        import fcntl

        if not ID_RE.fullmatch(task_id) or not step or not status:
            raise ValueError("invalid event identity")
        key = idempotency_key or hashlib.sha256(
            f"{task_id}|{step}|{status}|{fields.get('execution_id', '')}".encode()).hexdigest()
        path = self.events / f"{task_id}.jsonl"
        with (self.events / ".events.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            previous = ""
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    existing = json.loads(line)
                    if existing.get("idempotency_key") == key:
                        return {**existing, "delivery": "DUPLICATE_IGNORED"}
                    previous = existing.get("event_hash", "")
            event = {"contract_version": EVENT_CONTRACT,
                     "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"kmcw:{key}")),
                     "idempotency_key": key, "task_id": task_id, "step": step,
                     "status": status, "observed_at": time.time(),
                     "sync_state": "UNSYNCED_LOCAL_SPOOL",
                     **{k: _scrub_event(fields[k]) for k in EVENT_FIELDS if k in fields},
                     "prev_event_hash": previous}
            event["event_hash"] = self._event_hash(event)
            with path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                output.flush()
                os.fsync(output.fileno())
            return {**event, "delivery": "APPENDED"}

    def read_events(self, task_id: str) -> dict:
        """Readback with hash-chain verification; tampering is reported, not hidden."""
        if not ID_RE.fullmatch(task_id):
            raise ValueError("invalid task id")
        path = self.events / f"{task_id}.jsonl"
        events, previous, intact = [], "", True
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if (event.get("prev_event_hash") != previous
                        or event.get("event_hash") != self._event_hash(event)):
                    intact = False
                previous = event.get("event_hash", "")
                events.append(event)
        latest = {}
        for event in events:
            latest[event["step"]] = event["status"]
        return {"task_id": task_id, "event_count": len(events), "chain_intact": intact,
                "progress": latest, "events": events,
                "spool_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                "central_sync": "UNSYNCED_NO_AUTHORIZED_CENTRAL_WRITER"}

    # -- checkpoints ------------------------------------------------------------
    def save_checkpoint(self, task_id: str, stage: str, data: dict) -> None:
        self._write_json(self.checkpoints / f"{task_id}.json",
                         {"task_id": task_id, "stage": stage, "data": data})

    def checkpoint_task(self, task: Task, stage: str, data: dict) -> None:
        """Persist original execution events before any derived memory."""
        with self._locked():
            self._assert_lease(task.task_id, task.lease_token)
            prior = self.load_checkpoint(task.task_id)
            if prior is None and (self.checkpoints / f"{task.task_id}.json").exists():
                raise QueueLeaseError("checkpoint unreadable")
            merged = {**(prior or {}).get("data", {}), **data,
                      "task_snapshot": task.to_dict(), "observed_at": time.time()}
            self.save_checkpoint(task.task_id, stage, merged)
            self._write_json(self.doing / f"{task.task_id}.json", task.to_dict())

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
