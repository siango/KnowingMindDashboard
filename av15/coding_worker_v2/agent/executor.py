"""V3 Phase 4: autonomous executor. Plan -> Execute -> Observe -> Verify.

Reuses QueueStore (authority), run_task (execution), receipts (evidence),
memory (dedup) and worktree (isolation). Local model fallback comes from
the Qwen provider; no paid calls; repair loops are bounded.
"""
from __future__ import annotations

import time
import hashlib
import json
import os
from pathlib import Path

from ..config import WorkerConfig
from ..pipeline import list_files
from ..providers import QwenProvider
from ..queue_store import QueueStore
from ..receipts import write_receipt
from ..run_once import run_task
from ..tasks import Task
from .auditor import (MissionAuditorError, SCOPE_DIGEST, context_digest,
                     run_mission_audit)
from .memory import classify, current_base_commit, fingerprint_for
from .planner import Plan, order_tasks, is_read_only
from .verification import VerificationError, create_handoff, wait_signed_proof
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
                 audit_fn=None, progress=None) -> dict:
    """Execute all planned tasks in dependency order. Returns a report."""
    progress = progress or (lambda **kw: None)
    progress(phase="ADMISSION_AUDIT", state="RUNNING", total=len(plan.tasks), done=0, unit="tasks")
    if is_read_only(plan.source_command):
        reason = "READ_ONLY_TOOL_RUNTIME_NOT_RECONCILED"
        progress(phase="ADMISSION", state="BLOCKED", reason=reason)
        return {"plan_id": plan.plan_id, "revision": plan.revision,
                "tasks": [{"task_id": t.task_id, "ns_id": namespaced_id(plan,t.task_id),
                           "status": "SKIPPED", "decision": "BLOCKED", "reason": reason} for t in plan.tasks],
                "done": 0, "reused": 0, "failed": 0, "skipped": len(plan.tasks),
                "status": "BLOCKED", "next_action": reason}
    if plan.owner_node != config.owner_node:
        raise AgentError("OWNER_MISMATCH",
                         f"plan={plan.owner_node} runtime={config.owner_node}")
    task_targets = [namespaced_id(plan, task.task_id) for task in plan.tasks]
    if len(task_targets) != len(set(task_targets)):
        blocked = [{"task_id": task.task_id, "decision": "BLOCKED",
                    "status": "SKIPPED", "reason": "DUPLICATE_TASK_TARGET"}
                   for task in plan.tasks]
        return {"plan_id": plan.plan_id, "revision": plan.revision,
                "tasks": blocked, "reused": 0, "done": 0, "failed": 0,
                "skipped": len(blocked), "status": "BLOCKED",
                "next_action": "RESOLVE_DUPLICATE_TARGETS"}
    audit_fn = audit_fn or run_mission_audit
    plan_scope = {"mission_id": plan.mission_id, "plan_id": plan.plan_id,
                  "revision": plan.revision, "owner_node": plan.owner_node,
                  "source_command": plan.source_command, "workspace": workspace,
                  "source_sha": current_base_commit(workspace),
                  "scope_digest": SCOPE_DIGEST, "plan_snapshot": plan.to_dict(),
                  "tasks": [task.to_dict() for task in plan.tasks]}
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
        progress(phase="TASK", task=namespaced_id(plan,planned.task_id), state="RUNNING", provider="qwen-local", qc="NOT_RUN", receipt="PENDING")
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
                                  audit_fn, plan_scope, progress))
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
        progress(phase="TASK_COMPLETE", done=report["done"]+report["reused"],
                 state="DONE" if entry.get("status") == "DONE" else "BLOCKED",
                 reason=entry.get("next_action",entry.get("reason","-")),
                 qc="PASSED" if entry.get("qc_passed") else "NOT_VERIFIED",
                 receipt="AVAILABLE" if entry.get("receipt") or entry.get("reused_receipt") else "PENDING",
                 reference=entry.get("receipt") or entry.get("reused_receipt") or "-")
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
                 ledger_path: Path | None, audit_fn, plan_scope: dict, progress) -> dict:
    if planned.model != "qwen-local":
        return {"decision": "BLOCKED", "status": "SKIPPED",
                "reason": "UNSUPPORTED_MODEL_ROUTE"}
    if planned.tools:
        return {"decision": "BLOCKED", "status": "SKIPPED",
                "reason": "TOOL_DISPATCH_UNAVAILABLE"}
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
                            "plan_digest": plan_scope["plan_digest"],
                            "scope_digest": SCOPE_DIGEST,
                            "fingerprint": fingerprint,
                            "workspace": workspace,
                            "approved_scope": {
                                "mission_id": plan.mission_id,
                                "plan_id": plan.plan_id,
                                "revision": plan.revision,
                                "owner_node": plan.owner_node,
                                "queue_task_id": ns_id,
                                "source_command": plan.source_command,
                                "plan_digest": plan_scope["plan_digest"],
                                "scope_digest": SCOPE_DIGEST,
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

    def before_mutation(current_task):
        context = {
            **plan_scope, "task": planned.to_dict(),
            "task_id": planned.task_id, "queue_task_id": ns_id,
            "repo": current_task.task_input.repo, "source_sha": base_commit,
            "allowed_paths": allowed_paths, "fingerprint": fingerprint,
            "queue_store": store, "lease_token": current_task.lease_token,
        }
        return _audit(audit_fn, "BEFORE_MUTATION", context)

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
                          history=history,
                          before_mutation_fn=before_mutation, progress=progress)
        if result.status == "DONE":
            break
        if result.failure_class == "LEASE_LOST" or not store.lease_valid(
                result.task_id, result.lease_token):
            entry.update({"status": "FAILED", "failure_class": "LEASE_LOST",
                          "next_action": "RECONCILE_LEASE"})
            return entry
        outcome = store.fail(result, result.failure_class or "UNKNOWN",
                             result.next_action,
                             retry=not (result.failure_class == "POLICY_DENY"
                                       and result.next_action == "AUDIT_GATE_BLOCKED"))
        result.status = "FAILED"
        if outcome == "failed":
            break
        sleep_fn(backoff_s(result.attempts))

    assert result is not None
    qc_report = None
    receipt = None
    if result.status == "DONE":
        progress(phase="QC_AND_INDEPENDENT_VERIFICATION", state="RUNNING", qc="RUNNING", model="-", tool="-")
        if qc_fn is None:
            from .qc import run_qc

            qc_fn = run_qc
        with store.keepalive(result) as lease_lost:
            patch_digest = (result.output or {}).get("patch_digest", "")
            test_evidence = list(result.evidence)
            audit_after = _audit(audit_fn, "AFTER_TEST", {
                **plan_scope, "task": planned.to_dict(),
                "task_id": planned.task_id, "queue_task_id": ns_id,
                "repo": repo, "source_sha": base_commit,
                "allowed_paths": allowed_paths, "fingerprint": fingerprint,
                "patch_digest": patch_digest, "test_evidence": test_evidence,
                "queue_store": store, "lease_token": result.lease_token,
            })
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

            result.status = "QC_PENDING"
            request_dir = state_dir / "verifier_handoff"
            candidate_receipt = write_receipt(receipts_dir, result)
            try:
                request_path, request = create_handoff(
                    request_dir, result, candidate_receipt, plan_scope,
                    result.owner_node, f"kmcw:{ns_id}")
                proof_path = request_path.with_name(
                    request_path.name.replace(".request.json", ".proof.json"))
                public_key = Path(os.environ.get(
                    "KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY", ""))
                proof = wait_signed_proof(
                    request_path, proof_path, public_key,
                    lambda: not lease_lost.is_set() and store.lease_valid(
                        result.task_id, result.lease_token))
                result.evidence.append({
                    "event": "independent_verification",
                    "request_digest": request["request_digest"],
                    "proof": proof,
                })
            except VerificationError as exc:
                request_path = locals().get("request_path")
                proof = None
                result.evidence.append({
                    "event": "verification_blocked", "reason": exc.reason,
                    "request": str(request_path) if request_path else "",
                })

            result.status = "DONE"
            result.next_action = "REVIEW_PATCH"
            qc_report = qc_fn(result, {
                "config": config, "workspace": workspace, "repo": repo,
                "store": store, "receipts_dir": receipts_dir,
                "receipt_path": str(candidate_receipt),
                "mission_audit": audit_after,
                "executor_id": result.owner_node,
                "executor_runner_id": f"kmcw:{ns_id}",
                "source_sha": base_commit,
                "independent_verification": proof, "progress": progress,
            })
            result.evidence.append({"event": "qc_checked", "qc": qc_report})
            result.status = "QC_PENDING"
            result.next_action = "BEFORE_CLOSE_AUDIT"
            close_receipt = write_receipt(receipts_dir, result)
            close_receipt_raw = close_receipt.read_bytes()
            close_receipt_data = json.loads(close_receipt_raw)
            audit_close = _audit(audit_fn, "BEFORE_CLOSE", {
                **plan_scope, "task": planned.to_dict(),
                "task_id": planned.task_id, "queue_task_id": ns_id,
                "repo": repo, "source_sha": base_commit,
                "allowed_paths": allowed_paths, "fingerprint": fingerprint,
                "patch_digest": patch_digest, "test_evidence": test_evidence,
                "queue_store": store, "lease_token": result.lease_token,
                "receipt_path": str(close_receipt),
                "receipt_sha256": hashlib.sha256(close_receipt_raw).hexdigest(),
                "receipt_version": close_receipt_data.get("receipt_version"),
                "qc_report": qc_report, "independent_verification": proof,
            })
            result.evidence.append({"event": "mission_audit",
                                    "stage": "BEFORE_CLOSE",
                                    "result": audit_close})
            if lease_lost.is_set() or not store.lease_valid(
                    result.task_id, result.lease_token):
                result.status = "FAILED"
                result.failure_class = "LEASE_LOST"
                result.next_action = "RECONCILE_LEASE"
            elif (audit_close.get("gate_passed") is True
                  and qc_report.get("passed") is True
                  and qc_report.get("independent_verified") is True
                  and isinstance(proof, dict)
                  and proof.get("authenticated") is True):
                result.status = "DONE"
                result.next_action = "REVIEW_PATCH"
                receipt = write_receipt(receipts_dir, result)
                store.complete(result)
            else:
                result.status = "FAILED"
                result.failure_class = "POLICY_DENY"
                result.next_action = (
                    "AWAIT_INDEPENDENT_VERIFICATION" if proof is None
                    else "QC_ESCALATE")
                store.fail(result, result.failure_class, result.next_action,
                           retry=False)
        if result.status == "FAILED" and result.failure_class != "LEASE_LOST":
            result.evidence.append({"event": "queue_outcome", "status": "BLOCKED",
                                    "next_action": result.next_action})
    progress(phase="RECEIPT", state="RUNNING", tool="-", model="-")
    if receipt is None:
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
    expected_binding = context_digest(stage, context)
    try:
        result = audit_fn(stage, context)
    except MissionAuditorError as exc:
        return {"stage": stage, "gate_passed": False,
                "reason": exc.reason, "detail": exc.detail}
    except Exception as exc:
        return {"stage": stage, "gate_passed": False,
                "reason": "AUDIT_ADAPTER_CRASH",
                "detail": type(exc).__name__}
    if not isinstance(result, dict):
        return {"stage": stage, "gate_passed": False,
                "reason": "AUDIT_RESULT_INVALID"}
    if result.get("context_digest") != expected_binding:
        return {"stage": stage, "gate_passed": False,
                "reason": "AUDIT_CONTEXT_MISMATCH"}
    return result


def _test_command(workspace: str) -> str:
    probe = Path(workspace)
    if (probe / "package.json").is_file():
        return "npm test -- --watchAll=false"
    return "python3 -m unittest"
