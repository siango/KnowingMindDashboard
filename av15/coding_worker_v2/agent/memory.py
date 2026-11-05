"""V3 Phase 2: task memory and deduplication.

Decisions: NEW / REUSE / RESUME / REVERIFY / REPAIR / MONITOR / BLOCKED.

Completion is never granted on natural-language similarity alone: REUSE
requires an exact deterministic fingerprint plus matching source revision,
fresh evidence and a recorded QC pass.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..tasks import ID_RE

DECISIONS = {"NEW", "REUSE", "RESUME", "REVERIFY", "REPAIR", "MONITOR", "BLOCKED"}
WS_RE = re.compile(r"\s+")
DEFAULT_FRESHNESS_TTL_S = 86400


def normalize_goal(goal: str) -> str:
    return WS_RE.sub(" ", (goal or "").strip().lower())


def fingerprint_for(goal: str, repo: str, base_commit: str) -> str:
    """Deterministic task fingerprint (sha256, hex)."""
    canonical = json.dumps({"goal": normalize_goal(goal), "repo": repo or "",
                            "base": base_commit or ""},
                           ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_base_commit(repo: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=15,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()[:64]


@dataclass(frozen=True)
class Classification:
    decision: str
    reason: str
    fingerprint: str
    evidence_refs: tuple = ()


def _receipt_qc_pass(receipt: dict) -> bool:
    for event in receipt.get("evidence", []):
        if isinstance(event, dict) and event.get("event") == "qc_checked":
            qc = event.get("qc", {})
            if isinstance(qc, dict) and qc.get("passed") is True:
                return True
    return False


def _iter_receipts(receipts_dir: Path):
    if not receipts_dir.is_dir():
        return
    versioned_tasks = set()
    for path in sorted(receipts_dir.glob("history/*/v*.json"), reverse=True):
        task_id = path.parent.name
        if task_id in versioned_tasks:
            continue
        versioned_tasks.add(task_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(data, dict)
                and data.get("schema") == "KM_CODING_RECEIPT_V1"
                and data.get("task_id") == task_id):
            yield path, data
    for path in sorted(receipts_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(data, dict)
                and data.get("schema") == "KM_CODING_RECEIPT_V1"
                and data.get("task_id") not in versioned_tasks):
            yield path, data


def terminal_conflicts(store, receipt: dict) -> list[dict]:
    """Report immutable terminal queue/receipt disagreement; never pick a winner."""
    task_id = receipt.get("task_id")
    if not isinstance(task_id, str) or not ID_RE.fullmatch(task_id):
        return [{"reason": "INVALID_RECEIPT_TASK_ID"}]
    conflicts = []
    for folder_name, folder in (("done", store.done), ("failed", store.failed)):
        path = folder / task_id / "task.json"
        if not path.exists():
            continue
        try:
            archived = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            conflicts.append({"task_id": task_id,
                              "queue_ref": f"queue:{folder_name}/{task_id}",
                              "reason": "TERMINAL_RECORD_UNREADABLE"})
            continue
        if not isinstance(archived, dict):
            conflicts.append({"task_id": task_id,
                              "queue_ref": f"queue:{folder_name}/{task_id}",
                              "reason": "TERMINAL_RECORD_UNREADABLE"})
            continue
        fields = [key for key in ("task_id", "mission_id", "owner_node", "input",
                                  "status", "attempts")
                  if archived.get(key) != receipt.get(key)]
        if folder_name != "done" and receipt.get("status") == "DONE":
            fields.append("terminal_location")
        if fields:
            conflicts.append({"task_id": task_id,
                              "queue_ref": f"queue:{folder_name}/{task_id}",
                              "reason": "QUEUE_RECEIPT_CONFLICT",
                              "fields": sorted(set(fields)),
                              "queue_status": archived.get("status"),
                              "receipt_status": receipt.get("status")})
    return conflicts


def canonical_repo(task_input: dict) -> str:
    """Base workspace for matching (worktree paths are execution detail)."""
    hints = task_input.get("hints", {})
    if isinstance(hints, dict) and hints.get("workspace"):
        return str(hints["workspace"])
    return str(task_input.get("repo", ""))


def _queued_fingerprints(store) -> list[dict]:
    """Fingerprints of live queue entries (inbox + doing)."""
    out = []
    for directory in (store.inbox, store.doing):
        for path in sorted(directory.glob("*.json")):
            if path.name.endswith(".lease.json"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            task_input = data.get("input", {})
            if not isinstance(task_input, dict):
                continue
            hints = task_input.get("hints", {})
            out.append({"task_id": data.get("task_id", path.stem),
                        "queue": directory.name,
                        "attempts": data.get("attempts", 0),
                        "max_attempts": data.get("max_attempts", 3),
                        "fingerprint": fingerprint_for(
                            str(task_input.get("goal", "")),
                            canonical_repo(task_input),
                            str(hints.get("base_commit", ""))
                            if isinstance(hints, dict) else "")})
    return out


def classify(goal: str, repo: str, base_commit: str, store,
             receipts_dir: Path, now: float | None = None,
             freshness_ttl_s: int = DEFAULT_FRESHNESS_TTL_S,
             task_id: str = "", max_attempts: int = 3) -> Classification:
    """Classify an intake task against SSOT (receipts + leases + queue)."""
    now = time.time() if now is None else now
    fingerprint = fingerprint_for(goal, repo, base_commit)
    if not goal or not goal.strip():
        return Classification("BLOCKED", "GOAL_EMPTY", fingerprint)
    if not repo or not Path(repo).is_dir():
        return Classification("BLOCKED", "REPO_MISSING", fingerprint)

    live = [entry for entry in _queued_fingerprints(store)
            if entry["fingerprint"] == fingerprint]
    for entry in live:
        if entry["queue"] == "doing":
            if store.lease_valid(entry["task_id"]):
                return Classification(
                    "MONITOR", f"ACTIVE_LEASE task={entry['task_id']}",
                    fingerprint, (f"queue:doing/{entry['task_id']}",))
            checkpoint = store.load_checkpoint(entry["task_id"])
            if checkpoint:
                return Classification(
                    "RESUME", f"CHECKPOINT stage={checkpoint.get('stage')}",
                    fingerprint, (f"queue:doing/{entry['task_id']}",
                                  f"checkpoint:{checkpoint.get('stage')}",))
            return Classification(
                "RESUME", "EXPIRED_LEASE_RECOVERABLE", fingerprint,
                (f"queue:doing/{entry['task_id']}",))
    for entry in live:
        if entry["queue"] == "inbox":
            return Classification(
                "MONITOR", f"QUEUED task={entry['task_id']}", fingerprint,
                (f"queue:inbox/{entry['task_id']}",))

    want_key = (normalize_goal(goal), repo or "")
    for path, receipt in _iter_receipts(receipts_dir):
        output = receipt.get("output", {})
        receipt_input = receipt.get("input", {}) or {}
        hints = receipt_input.get("hints", {})
        receipt_base = hints.get("base_commit", "") if isinstance(hints, dict) else ""
        receipt_key = (normalize_goal(str(receipt_input.get("goal", ""))),
                       canonical_repo(receipt_input))
        if receipt_key != want_key:
            continue
        ref = f"receipt:{path.relative_to(receipts_dir)}"
        conflicts = terminal_conflicts(store, receipt)
        if conflicts:
            refs = (ref, *(item.get("queue_ref", "") for item in conflicts))
            reason = conflicts[0].get("reason", "QUEUE_RECEIPT_CONFLICT")
            return Classification("BLOCKED", reason, fingerprint, refs)
        if receipt.get("status") == "DONE":
            age = now - path.stat().st_mtime
            if receipt_base != base_commit:
                return Classification("REVERIFY", "BASE_COMMIT_CHANGED",
                                      fingerprint, (ref,))
            if age > freshness_ttl_s:
                return Classification("REVERIFY", f"EVIDENCE_STALE age_s={int(age)}",
                                      fingerprint, (ref,))
            if not _receipt_qc_pass(receipt):
                return Classification("REVERIFY", "QC_PASS_NOT_RECORDED",
                                      fingerprint, (ref,))
            _ = output
            return Classification("REUSE", "FINGERPRINT_REV_FRESH_QC_MATCH",
                                  fingerprint, (ref,))
        attempts = int(receipt.get("attempts", 0) or 0)
        if attempts < max_attempts:
            return Classification(
                "REPAIR", f"PRIOR_{receipt.get('failure_class', 'UNKNOWN')}",
                fingerprint, (ref,))
        return Classification("BLOCKED", "RETRIES_EXHAUSTED", fingerprint, (ref,))

    _ = task_id
    return Classification("NEW", "NO_PRIOR_RECORD", fingerprint)
