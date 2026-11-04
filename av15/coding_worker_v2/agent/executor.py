"""V3 Phase 4: autonomous executor. Plan -> Execute -> Observe -> Verify.

Reuses QueueStore (authority), run_task (execution), receipts (evidence),
memory (dedup) and worktree (isolation). Local model fallback comes from
the Qwen provider; no paid calls; repair loops are bounded.
"""
from __future__ import annotations

import time
import hashlib
import json
from pathlib import Path

from ..config import WorkerConfig
from ..pipeline import list_files
from ..providers import QwenProvider
from ..queue_store import QueueStore
from ..receipts import write_receipt
from ..run_once import run_task
from ..tasks import Task
from .auditor import MissionAuditorError, run_mission_audit
from .memory import classify, current_base_commit, fingerprint_for
from .planner import Plan, order_tasks
from .worktree import WorktreeRegistry


class AgentError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail[:300]}" if detail else reason)
        self.reason = reason
        self.detail = detail[:2000]


def qwen_chain(config: WorkerConfig) -> list:
    """Muse-free default chain: Qwen primary+fallback only."""
    provider = QwenProvider(config)
    try:
        ok, _ = provider.is_available()
    except Exception:
        ok = False
    return [provider] if ok else []


def backoff_s(attempt: int, cap: int = 60) -> int:
    return min(cap, 2 ** max(0, attempt))


def namespaced_id(plan: Plan, task_id: str) -> str:
    return f"{plan.plan_id}-{task_id}"[:120]


def execute_plan(plan: Plan, config: WorkerConfig, store: QueueStore,
                 receipts_dir: Path, state_dir: Path, workspace: str,
                 history: list[dict] | None = None,
                 use_worktree: bool = True,
                 qc_fn=None, sleep_fn=time.sleep,
                 ledger_path: Path | None = None,
                 audit_fn=None) -> dict:
    """Execute all planned tasks in dependency order. Returns a report."""
    if plan.owner_node != config.owner_node:
        raise AgentError("OWNER_MISMATCH",
                         f"plan={plan.owner_node} runtime={config.owner_node}")
    audit_fn = audit_fn or run_mission_audit
    plan_scope = {"mission_id": plan.mission_id, "plan_id": plan.plan_id,
                  "revision": plan.revision, "owner_node": plan.owner_node,
                  "source_command": plan.source_command, "workspace": workspace}
    plan_scope["plan_digest"] = hashlib.sha256(json.dumps(
        plan.to_dict(), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()
    admission = _audit(audit_fn, "ADMIT", plan_scope)
    if admission.get("gate_passed") is not True:
        blocked = [{"task_id": t.task_id, "decision": "BLOCKED",
                    "status": "SKIPPED",
                    "reason": admission.get("reason", "AUDIT_GATE_BLOCKED")}
                   for t in plan.tasks]
        return {"plan_id": plan.plan_id, "revision": plan.revision,
                "tasks": blocked, "reused": 0, "done": 0, "failed": 0,
                "skipped": len(blocked), "status": "BLOCKED",
                "next_action": "AUDITOR_UNAVAILABLE"}
    history = history if history is not None else []
    ordered = order_tasks(plan)
    registry = WorktreeRegistry(state_dir) if use_worktree else None
    report: dict = {"plan_id": plan.plan_id, "revision": plan.revision,
                    "tasks": [], "reused": 0, "done": 0, "failed": 0,
                    "skipped": 0}
    failed_ids: set[str] = set()
    for planned in ordered:
        entry: dict = {"task_id": planned.task_id,
                       "ns_id": namespaced_id(plan, planned.task_id)}
        if any(dep in failed_ids for dep in planned.deps):
            entry.update({"decision": "BLOCKED", "reason": "DEP_FAILED",
                          "status": "SKIPPED"})
            report["tasks"].append(entry)
            report["skipped"] += 1
            failed_ids.add(planned.task_id)
            continue
        entry.update(_execute_one(planned, entry["ns_id"], plan, config, store,
                                  receipts_dir, state_dir, registry, workspace,
                                  history, qc_fn, sleep_fn, ledger_path,
                                  audit_fn, plan_scope))
        report["tasks"].append(entry)
        if entry.get("decision") == "REUSE":
            report["reused"] += 1
        elif entry.get("status") == "DONE":
            report["done"] += 1
        elif entry.get("status") == "FAILED":
            report["failed"] += 1
            failed_ids.add(planned.task_id)
        else:
            report["skipped"] += 1
            failed_ids.add(planned.task_id)
    completed = report["done"] + report["reused"]
    if report["failed"]:
        report["status"] = "PARTIAL" if completed else "FAILED"
        report["next_action"] = "ESCALATE_FAILED_TASKS"
    elif report["skipped"]:
        report["status"] = "PARTIAL" if completed else "BLOCKED"
        report["next_action"] = "RESOLVE_BLOCKED_TASKS"
    else:
        report["status"] = "DONE"
        report["next_action"] = "REVIEW_RECEIPTS"
    return report


def _execute_one(planned, ns_id: str, plan: Plan, config: WorkerConfig,
                 store: QueueStore, receipts_dir: Path, state_dir: Path,
                 registry: WorktreeRegistry | None, workspace: str,
                 history: list[dict], qc_fn, sleep_fn,
                 ledger_path: Path | None, audit_fn, plan_scope: dict) -> dict:
    allowed_paths = list(planned.allowed_paths)
    if not allowed_paths:
        return {"decision": "BLOCKED", "status": "SKIPPED",
                "reason": "APPROVED_FILE_ALLOWLIST_REQUIRED"}
    workspace_files = set(list_files(workspace))
    missing_paths = [path for path in allowed_paths
                     if path not in workspace_files]
    if missing_paths:
        return {"decision": "BLOCKED", "status": "SKIPPED",
                "reason": "APPROVED_PATH_NOT_FOUND",
                "paths": missing_paths[:10]}
    base_commit = current_base_commit(workspace)
    fingerprint = fingerprint_for(planned.goal, workspace, base_commit)
    decision = classify(planned.goal, workspace, base_commit, store,
                        receipts_dir, task_id=ns_id,
                        max_attempts=planned.max_repairs + 1)
    entry: dict = {"decision": decision.decision, "reason": decision.reason,
                   "fingerprint": fingerprint[:16],
                   "evidence_refs": list(decision.evidence_refs)}
    if decision.decision in ("BLOCKED", "MONITOR"):
        entry["status"] = "SKIPPED"
        return entry
    if decision.decision == "REUSE":
        entry["status"] = "DONE"
        entry["reused_receipt"] = (decision.evidence_refs[0]
                                   if decision.evidence_refs else "")
        history.append({"role": "user", "content": planned.goal[:2000]})
        history.append({"role": "assistant",
                        "content": f"REUSED {entry['reused_receipt']}"})
        return entry
    if decision.decision == "RESUME":
        recovered = store.recover()
        entry["recovered"] = recovered

    audit_before = _audit(audit_fn, "BEFORE_MUTATION",
                          {**plan_scope, "task": planned.to_dict(),
                           "fingerprint": fingerprint})
    if audit_before.get("gate_passed") is not True:
        entry.update({"decision": "BLOCKED", "status": "SKIPPED",
                      "reason": audit_before.get(
                          "reason", "AUDIT_GATE_BLOCKED")})
        return entry
    entry["mission_audit_admit"] = audit_before
    repo = workspace
    if registry is not None:
        try:
            worktree = registry.create(workspace, ns_id)
            repo = worktree["path"]
            entry["worktree"] = repo
            entry["branch"] = worktree["branch"]
        except Exception as exc:
            entry.update({"decision": "BLOCKED", "status": "SKIPPED",
                          "reason": f"WORKTREE_{type(exc).__name__}"})
            return entry

    task = Task.from_dict({
        "mission_id": plan.mission_id, "task_id": ns_id,
        "owner_node": plan.owner_node,
        "input": {"repo": repo, "goal": planned.goal,
                  "test_command": _test_command(workspace),
                  "preferred_provider": "qwen-local",
                  "max_repairs": planned.max_repairs,
                  "hints": {"base_commit": base_commit, "plan_id": plan.plan_id,
                            "fingerprint": fingerprint,
                            "workspace": workspace,
                            "approved_scope": {
                                "mission_id": plan.mission_id,
                                "plan_id": plan.plan_id,
                                "revision": plan.revision,
                                "owner_node": plan.owner_node,
                                "queue_task_id": ns_id,
                                "source_command": plan.source_command,
                                "task_id": planned.task_id,
                                "goal": planned.goal,
                                "acceptance": list(planned.acceptance),
                                "base_commit": base_commit,
                                "allowed_paths": allowed_paths},
                            "acceptance": list(planned.acceptance),
                            "complexity": planned.complexity,
                            "prior": list(decision.evidence_refs)[:3]}}})
    _, created = store.enqueue(task)
    if not created:
        entry.update({"decision": "MONITOR", "reason": "ALREADY_QUEUED",
                      "status": "SKIPPED"})
        return entry

    chain = qwen_chain(config)
    history.append({"role": "user", "content": planned.goal[:2000]})
    result = None
    while True:
        store.recover()
        claimed = store.claim()
        if claimed is None or claimed.task_id != ns_id:
            # Never disturb a foreign claim: it stays leased and the
            # executor reports the conflict honestly.
            entry.update({"status": "FAILED", "reason": "CLAIM_MISMATCH"})
            return entry
        result = run_task(claimed, store, config, ledger_path, chain=chain,
                          history=history)
        if result.status == "DONE":
            break
        if result.failure_class == "LEASE_LOST" or not store.lease_valid(
                result.task_id, result.lease_token):
            entry.update({"status": "FAILED", "failure_class": "LEASE_LOST",
                          "next_action": "RECONCILE_LEASE"})
            return entry
        outcome = store.fail(result, result.failure_class or "UNKNOWN",
                             result.next_action)
        result.status = "FAILED"
        if outcome == "failed":
            break
        sleep_fn(backoff_s(result.attempts))

    assert result is not None
    qc_report = None
    if result.status == "DONE":
        if qc_fn is None:
            from .qc import run_qc

            qc_fn = run_qc
        with store.keepalive(result) as lease_lost:
            audit_after = _audit(
                audit_fn, "AFTER_TEST",
                {**plan_scope, "task": planned.to_dict(),
                 "fingerprint": fingerprint,
                 "receipt_task_id": result.task_id,
                 "patch_digest": (result.output or {}).get("patch_digest")})
            result.evidence.append({"event": "mission_audit",
                                    "stage": "AFTER_TEST",
                                    "result": audit_after})
            if audit_after.get("gate_passed") is not True:
                result.status = "FAILED"
                result.failure_class = "POLICY_DENY"
                result.next_action = "AUDIT_ESCALATE"
                store.fail(result, result.failure_class, result.next_action,
                           retry=False)
                receipt = write_receipt(receipts_dir, result)
                entry.update({"status": result.status,
                              "failure_class": result.failure_class,
                              "next_action": result.next_action,
                              "receipt": str(receipt)})
                return entry
            audit_close = _audit(
                audit_fn, "BEFORE_CLOSE",
                {**plan_scope, "task": planned.to_dict(),
                 "fingerprint": fingerprint,
                 "receipt_task_id": result.task_id,
                 "patch_digest": (result.output or {}).get("patch_digest")})
            result.evidence.append({"event": "mission_audit",
                                    "stage": "BEFORE_CLOSE",
                                    "result": audit_close})
            result.status = "QC_PENDING"
            provisional_receipt = write_receipt(receipts_dir, result)
            result.evidence.append({"event": "receipt_written",
                                    "receipt": str(provisional_receipt)})
            result.status = "DONE"
            qc_report = qc_fn(result, {"config": config, "workspace": workspace,
                                       "repo": repo, "store": store,
                                       "receipts_dir": receipts_dir,
                                       "receipt_path": str(provisional_receipt),
                                       "mission_audit": audit_close,
                                       "executor_id": result.owner_node,
                                       "executor_runner_id": f"kmcw:{ns_id}"})
            result.evidence.append({"event": "qc_checked", "qc": qc_report})
            if lease_lost.is_set() or not store.lease_valid(
                    result.task_id, result.lease_token):
                entry.update({"status": "FAILED", "failure_class": "LEASE_LOST",
                              "next_action": "RECONCILE_LEASE",
                              "qc_passed": False})
                return entry
            if (audit_close.get("gate_passed") is True
                    and qc_report.get("passed") is True
                    and qc_report.get("independent_verified") is True):
                store.complete(result)
            else:
                result.status = "FAILED"
                result.failure_class = "TEST_FAIL"
                result.next_action = (
                    "AUDIT_ESCALATE" if audit_close.get("gate_passed") is not True
                    else "QC_ESCALATE")
                store.fail(result, result.failure_class, result.next_action,
                           retry=False)
    receipt = write_receipt(receipts_dir, result)
    history.append({"role": "assistant",
                    "content": f"{result.status} {ns_id} "
                               f"{result.failure_class}"[:2000]})
    entry.update({"status": result.status, "failure_class": result.failure_class,
                  "next_action": result.next_action,
                  "changed": (result.output or {}).get("changed", []),
                  "receipt": str(receipt), "attempts": result.attempts,
                  "qc_passed": bool(qc_report.get("passed")) if qc_report else None})
    return entry


def _audit(audit_fn, stage: str, context: dict) -> dict:
    try:
        result = audit_fn(stage, context)
    except MissionAuditorError as exc:
        return {"stage": stage, "gate_passed": False,
                "reason": exc.reason, "detail": exc.detail}
    if not isinstance(result, dict):
        return {"stage": stage, "gate_passed": False,
                "reason": "AUDIT_RESULT_INVALID"}
    return result


def _test_command(workspace: str) -> str:
    probe = Path(workspace)
    if (probe / "package.json").is_file():
        return "npm test -- --watchAll=false"
    return "python3 -m unittest"
