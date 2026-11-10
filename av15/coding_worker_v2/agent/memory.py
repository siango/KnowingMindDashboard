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

from ..receipts import _scrub
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


def receipt_verified(receipt: dict) -> bool:
    source = receipt.get("source_sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        return False
    qc = [e.get("qc", {}) for e in receipt.get("evidence", [])
          if isinstance(e, dict) and e.get("event") == "qc_checked"]
    proofs = [e.get("proof", {}) for e in receipt.get("evidence", [])
              if isinstance(e, dict) and e.get("event") == "independent_verification"]
    return (receipt.get("status") == "DONE"
            and any(q.get("passed") is True and q.get("independent_verified") is True
                    for q in qc)
            and any(p.get("authenticated") is True and p.get("status") == "PASS"
                    and p.get("source_sha") == source
                    and p.get("task_id") == receipt.get("task_id")
                    and re.fullmatch(r"[0-9a-f]{64}", str(p.get("signature_sha256", "")))
                    for p in proofs))


def workspace_digest(repo: str, paths: list[str]) -> str:
    root = Path(repo).resolve()
    digest = hashlib.sha256()
    for rel in sorted(paths):
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("checkpoint file missing or outside workspace")
        digest.update(rel.encode("utf-8") + b"\0")
        with path.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def work_case(receipt: dict, reference: str, original_sha256: str) -> dict:
    """Derived four-part view; original receipts/events remain the authority."""
    events = [e for e in receipt.get("evidence", []) if isinstance(e, dict)]
    failures = [e for e in events if e.get("event") in
                {"edit_failed", "verification_blocked", "provider_paused"}]
    tests = [e.get("tests", {}) for e in events if e.get("event") == "tests_run"]
    output = receipt.get("output") or {}
    state = ("VERIFIED_PASS" if receipt_verified(receipt) else
             "EXECUTION_DONE" if receipt.get("status") == "DONE" else
             "BLOCKED" if receipt.get("status") in {"BLOCKED", "QC_PENDING"} else "FAILED")
    return {
        "task_id": receipt.get("task_id"), "source_sha": receipt.get("source_sha"),
        "timestamp": receipt.get("recorded_at"),
        "provenance": {"original_ref": reference, "sha256": original_sha256,
                       "classification": "DERIVED_WORK_MEMORY"},
        "PROBLEM": {"goal": receipt.get("input", {}).get("goal", ""),
                    "failure_class": receipt.get("failure_class"),
                    "observations": failures[-5:], "classification": "FACT"},
        "CAUSE": {"evidenced": failures[-5:],
                  "hypotheses": [], "root_cause_status": "UNRESOLVED"
                  if failures else "NOT_ASSERTED"},
        "SOLUTION_PATH": {"changed": output.get("changed", []),
                          "patch_digest": output.get("patch_digest"),
                          "provider": output.get("provider"),
                          "model": output.get("model"),
                          "test_command": receipt.get("input", {}).get("test_command"),
                          "tests": tests, "decision_summary": [
                              e for e in events if e.get("event") in
                              {"route_selected", "strategy_selected", "provider_switch"}]},
        "RESULT": {"state": state, "next_action": receipt.get("next_action"),
                   "classification": "FACT", "tests": tests},
    }


def lesson_tags(receipt: dict) -> list[str]:
    words = set(re.findall(r"[a-z][a-z0-9_]{2,}", normalize_goal(
        str(receipt.get("input", {}).get("goal", "")))))
    words -= {"the", "and", "for", "with", "fix", "that", "from", "this"}
    words.update(Path(path).suffix.lstrip(".") for path in
                 (receipt.get("output") or {}).get("changed", [])
                 if isinstance(path, str) and Path(path).suffix)
    return sorted(words)[:40]


def retrieve_work(goal, repo, source_sha, store, receipts_dir, limit=5, *,
                  account_id=None, ranking="baseline") -> list[dict]:
    if ranking not in {"baseline", "qc"}:
        raise ValueError("unknown memory ranking")
    records = []
    for path, receipt in _iter_receipts(receipts_dir):
        if not receipt or canonical_repo(receipt.get("input", {})) != repo:
            continue
        if (account_id is not None
                and receipt["input"].get("hints", {}).get("account_id") != account_id):
            continue
        previous_goal = normalize_goal(receipt.get("input", {}).get("goal", ""))
        exact = previous_goal == normalize_goal(goal)
        overlap = set(previous_goal.split()) & set(normalize_goal(goal).split())
        if not exact and not overlap:
            continue
        if terminal_conflicts(store, receipt):
            continue
        case = work_case(receipt, str(path), hashlib.sha256(path.read_bytes()).hexdigest())
        case["verified_for_current_source"] = (
            receipt.get("source_sha") == source_sha
            and exact and receipt_verified(receipt))
        case["retrieval_only"] = True
        case["tags"] = lesson_tags(receipt)
        case["qc_score"] = 1 if receipt_verified(receipt) else 0
        tag_overlap = len(set(re.findall(r"[a-z][a-z0-9_]{2,}",
                                        normalize_goal(goal))) & set(case["tags"]))
        score = ((exact, len(overlap)) if ranking == "baseline" else
                 (exact, tag_overlap, case["qc_score"], len(overlap)))
        case["retrieval_score"] = list(score)
        records.append((score, case))
    return [case for _, case in sorted(records, key=lambda row: row[0],
                                       reverse=True)[:limit]]


def strategy_effectiveness(store, receipts_dir) -> dict:
    outcomes = {}
    for _, receipt in _iter_receipts(receipts_dir):
        if not receipt or terminal_conflicts(store, receipt):
            continue
        provider = (receipt.get("output") or {}).get("provider")
        if not provider:
            continue
        row = outcomes.setdefault(provider, {"verified": 0, "failed": 0,
                                              "execution_done": 0})
        if receipt_verified(receipt):
            row["verified"] += 1
        elif receipt.get("status") == "FAILED":
            row["failed"] += 1
        elif receipt.get("status") == "DONE":
            row["execution_done"] += 1
    return outcomes


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
            yield path, None
            continue
        if (isinstance(data, dict)
                and data.get("schema") == "KM_CODING_RECEIPT_V1"
                and data.get("task_id") == task_id):
            yield path, data
        else:
            yield path, None
    for path in sorted(receipts_dir.glob("*.json")):
        if path.stem in versioned_tasks:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            yield path, None
            continue
        if (isinstance(data, dict)
                and data.get("schema") == "KM_CODING_RECEIPT_V1"
                and data.get("task_id") not in versioned_tasks):
            yield path, data
        else:
            yield path, None


def _iter_terminal_archives(store):
    for folder_name, folder in (("done", store.done), ("failed", store.failed)):
        for task_dir in sorted(folder.iterdir()):
            if not task_dir.is_dir():
                continue
            path = task_dir / "task.json"
            try:
                archived = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                yield folder_name, path, None
                continue
            if not isinstance(archived, dict) or archived.get("task_id") != task_dir.name:
                yield folder_name, path, None
            else:
                yield folder_name, path, archived


def terminal_conflicts(store, receipt: dict) -> list[dict]:
    """Report immutable terminal queue/receipt disagreement; never pick a winner."""
    task_id = receipt.get("task_id")
    if not isinstance(task_id, str) or not ID_RE.fullmatch(task_id):
        return [{"reason": "INVALID_RECEIPT_TASK_ID"}]
    conflicts = []
    expected_folder = {"DONE": "done", "FAILED": "failed"}.get(
        receipt.get("status"))
    expected_path = (getattr(store, expected_folder) / task_id / "task.json"
                     if expected_folder else None)
    any_archive = any((folder / task_id / "task.json").exists()
                      for folder in (store.done, store.failed))
    if (expected_path is not None and not expected_path.is_file()
            and not any_archive):
        conflicts.append({"task_id": task_id,
                          "queue_ref": f"queue:{expected_folder}/{task_id}",
                          "reason": "TERMINAL_ARCHIVE_MISSING"})
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
                                  "output", "evidence", "status", "next_action",
                                  "failure_class", "attempts")
                  if _scrub(archived.get(key)) != receipt.get(key)]
        expected_status = "DONE" if folder_name == "done" else "FAILED"
        if (folder_name != expected_folder or archived.get("status") != expected_status
                or receipt.get("status") != expected_status):
            fields.append("terminal_location")
        if archived.get("lease_token_sha256") != receipt.get("lease_token_sha256"):
            fields.append("lease_token_sha256")
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
                        "account_id": hints.get("account_id") if isinstance(hints, dict) else None,
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
             task_id: str = "", max_attempts: int = 3,
             account_id=None) -> Classification:
    """Classify an intake task against SSOT (receipts + leases + queue)."""
    now = time.time() if now is None else now
    fingerprint = fingerprint_for(goal, repo, base_commit)
    if not goal or not goal.strip():
        return Classification("BLOCKED", "GOAL_EMPTY", fingerprint)
    if not repo or not Path(repo).is_dir():
        return Classification("BLOCKED", "REPO_MISSING", fingerprint)

    queued = [entry for entry in _queued_fingerprints(store)
              if account_id is None or entry["account_id"] == account_id]
    live = [entry for entry in queued
            if entry["fingerprint"] == fingerprint]
    for entry in queued:
        if entry["task_id"] == task_id and entry["fingerprint"] != fingerprint:
            return Classification("BLOCKED", "SOURCE_OR_TASK_SCOPE_MISMATCH",
                                  fingerprint, (f"queue:{entry['queue']}/{task_id}",))
    for entry in live:
        if entry["queue"] == "doing":
            if store.lease_valid(entry["task_id"]):
                return Classification(
                    "MONITOR", f"ACTIVE_LEASE task={entry['task_id']}",
                    fingerprint, (f"queue:doing/{entry['task_id']}",))
            if task_id and entry["task_id"] != task_id:
                return Classification(
                    "MONITOR", f"RESUME_ORIGINAL_TASK task={entry['task_id']}",
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
            if entry["task_id"] == task_id and store.load_checkpoint(task_id):
                return Classification("RESUME", "CHECKPOINT_READY",
                                      fingerprint, (f"queue:inbox/{task_id}",))
            return Classification(
                "MONITOR", f"QUEUED task={entry['task_id']}", fingerprint,
                (f"queue:inbox/{entry['task_id']}",))

    matching_archives = []
    for folder_name, path, archived in _iter_terminal_archives(store):
        if archived is None:
            return Classification("BLOCKED", "TERMINAL_RECORD_UNREADABLE",
                                  fingerprint,
                                  (f"queue:{folder_name}/{path.parent.name}",))
        archived_input = archived.get("input", {})
        if not isinstance(archived_input, dict):
            return Classification("BLOCKED", "TERMINAL_RECORD_UNREADABLE",
                                  fingerprint,
                                  (f"queue:{folder_name}/{path.parent.name}",))
        if (account_id is not None
                and archived_input.get("hints", {}).get("account_id") != account_id):
            continue
        archive_key = (normalize_goal(str(archived_input.get("goal", ""))),
                       canonical_repo(archived_input))
        if archive_key == (normalize_goal(goal), repo or ""):
            matching_archives.append((folder_name, path))

    want_key = (normalize_goal(goal), repo or "")
    for path, receipt in _iter_receipts(receipts_dir):
        if receipt is None:
            return Classification("BLOCKED", "RECEIPT_HISTORY_UNREADABLE",
                                  fingerprint,
                                  (f"receipt:{path.relative_to(receipts_dir)}",))
        output = receipt.get("output", {})
        receipt_input = receipt.get("input", {}) or {}
        hints = receipt_input.get("hints", {})
        if account_id is not None and hints.get("account_id") != account_id:
            continue
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
        if receipt.get("status") in {"QC_PENDING", "VERIFY_PENDING"}:
            return Classification("BLOCKED", "AWAIT_INDEPENDENT_VERIFICATION",
                                  fingerprint, (ref,))
        if receipt.get("status") == "DONE":
            age = now - path.stat().st_mtime
            if receipt_base != base_commit:
                return Classification("REVERIFY", "BASE_COMMIT_CHANGED",
                                      fingerprint, (ref,))
            if age > freshness_ttl_s:
                return Classification("REVERIFY", f"EVIDENCE_STALE age_s={int(age)}",
                                      fingerprint, (ref,))
            if not receipt_verified(receipt):
                return Classification("REVERIFY", "QC_PASS_NOT_RECORDED",
                                      fingerprint, (ref,))
            base_digest = hints.get("base_workspace_digest")
            if base_digest:
                paths = hints.get("approved_scope", {}).get("allowed_paths", [])
                try:
                    same_content = paths and workspace_digest(repo, paths) == base_digest
                except (ValueError, OSError):
                    same_content = False
                if not same_content:
                    return Classification("REVERIFY", "WORKSPACE_CONTENT_CHANGED",
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

    if matching_archives:
        refs = tuple(f"queue:{folder}/{path.parent.name}"
                     for folder, path in matching_archives)
        return Classification("BLOCKED", "TERMINAL_RECEIPT_MISSING",
                              fingerprint, refs)

    _ = task_id
    return Classification("NEW", "NO_PRIOR_RECORD", fingerprint)
