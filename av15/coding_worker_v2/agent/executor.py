"""V3 Phase 4: autonomous executor. Plan -> Execute -> Observe -> Verify.

Reuses QueueStore (authority), run_task (execution), receipts (evidence),
memory (dedup) and worktree (isolation). Qwen-first proposals, explicitly
authorized external fallbacks, budget gates and bounded repair loops.
"""
from __future__ import annotations

import time
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from ..config import WorkerConfig
from ..pipeline import list_files
from ..providers import QwenProvider, execution_chain
from ..queue_store import QueueStore
from ..receipts import write_receipt, read_receipt
from ..run_once import run_task
from ..tasks import Task
from .auditor import (MissionAuditorError, SCOPE_DIGEST, context_digest,
                     run_mission_audit)
from .memory import (classify, current_base_commit, fingerprint_for, retrieve_work,
                     workspace_digest)
from .planner import Plan, order_tasks, is_read_only
from .verification import VerificationError, create_handoff, wait_signed_proof
from .worktree import WorktreeRegistry
from .sandbox import policy_decision


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


DIFFICULTY_RUBRIC = "KMCW_DIFFICULTY_PRE_V1"


def frozen_difficulty(planned, workspace: str, test_command: str) -> dict:
    """Pre-execution D1-D5 (1 low .. 3 high); post-hoc difficulty is recorded separately."""
    paths = list(planned.allowed_paths)
    security = policy_decision("edit", "difficulty", workspace, paths=paths,
                               approved_paths=paths, isolated=True)["decision"]
    return {"rubric": DIFFICULTY_RUBRIC,
            "D1_scope": 1 if len(paths) <= 1 else 2 if len(paths) <= 3 else 3,
            "D2_dependencies": min(3, 1 + len(planned.deps)),
            "D3_security": 1 if security == "AUTO_APPROVE" else 3,
            "D4_testing": 1 if test_command else 3,
            "D5_uncertainty": {"S": 1, "M": 2, "L": 3}.get(planned.complexity, 3)}


def _event_context(plan: Plan, ns_id: str, provider_policy, source_sha: str) -> dict:
    policy = provider_policy or {}
    return {"account_id": policy.get("account_id"), "mission_id": plan.mission_id,
            "command_id": plan.plan_id, "parent_command_id": policy.get("parent_command_id"),
            "prompt_version": f"KM_TASK_PLAN_V1:r{plan.revision}:{plan.engine}",
            "owner_node": plan.owner_node, "source_sha": source_sha,
            "execution_id": ns_id, "provenance": "KMCW executor"}


def _gate_status(result: dict) -> str:
    return "PASS" if result.get("gate_passed") is True else "BLOCKED"


def _gate_detail(result: dict) -> dict:
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    return {"reason": result.get("reason"), "detail": result.get("detail"),
            "verdict": report.get("verdict"),
            "observed_requirements": report.get("observed_requirements"),
            "requirements_total": report.get("requirements_total"),
            "global_findings": [f.get("code") for f in report.get("global_findings", [])
                                if isinstance(f, dict)],
            "context_digest": result.get("context_digest")}


def emit_gate(store, ns_id: str, stage: str, result: dict, context: dict) -> dict:
    detail = _gate_detail(result)
    key = hashlib.sha256(json.dumps(
        [ns_id, stage, _gate_status(result), context.get("source_sha"),
         detail["reason"], detail["global_findings"], detail["context_digest"]],
        sort_keys=True).encode()).hexdigest()
    return store.append_event(ns_id, f"gate.{stage}", _gate_status(result), key,
                              gate=detail, **context)


def learning_record(receipt: dict | None = None, *, problem: str = "", cause: list | None = None,
                    criterion: str = "", receipt_ref: str = "", receipt_sha: str = "") -> dict:
    """Ariyasacca Four as an engineering-analysis metaphor, not doctrinal truth."""
    from .memory import receipt_verified, work_case
    if receipt is not None:
        case = work_case(receipt, receipt_ref, receipt_sha)
        verified = receipt_verified(receipt)
        return {"framework": "FOUR_TRUTHS_ENGINEERING_METAPHOR_V1",
                "dukkha_problem": case["PROBLEM"], "samudaya_observed_cause": case["CAUSE"],
                "nirodha_success_criterion": receipt.get("input", {}).get("hints", {}).get("acceptance"),
                "magga_path": case["SOLUTION_PATH"] if verified else None,
                "magga_state": "PROVEN_BY_VERIFIED_RECEIPT" if verified else "UNPROVEN",
                "result": case["RESULT"], "receipt_ref": receipt_ref, "receipt_sha256": receipt_sha,
                "generalization": "NOT_AUTO_PERSISTED"}
    return {"framework": "FOUR_TRUTHS_ENGINEERING_METAPHOR_V1",
            "dukkha_problem": problem, "samudaya_observed_cause": cause or [],
            "nirodha_success_criterion": criterion, "magga_path": None,
            "magga_state": "UNPROVEN", "receipt_ref": None,
            "generalization": "NOT_AUTO_PERSISTED"}


def record_receipt(store, receipts_dir: Path, result, context: dict, terminal: bool = False) -> Path:
    """Existing write_receipt plus an immutable receipt event (and learning when terminal)."""
    path = write_receipt(receipts_dir, result)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    store.append_event(result.task_id, "receipt.written", str(result.status),
                       hashlib.sha256(f"receipt|{digest}".encode()).hexdigest(),
                       receipt_uri=str(path), receipt_sha256=digest,
                       next_action=result.next_action, **context)
    if terminal:
        learning = learning_record(json.loads(raw), receipt_ref=str(path), receipt_sha=digest)
        store.append_event(result.task_id, "learning.recorded",
                           "PROVEN" if learning["magga_state"] == "PROVEN_BY_VERIFIED_RECEIPT"
                           else "UNPROVEN",
                           hashlib.sha256(f"learning|{digest}".encode()).hexdigest(),
                           learning=learning,
                           receipt_uri=str(path), receipt_sha256=digest, **context)
    return path


def execute_plan(plan: Plan, config: WorkerConfig, store: QueueStore,
                 receipts_dir: Path, state_dir: Path, workspace: str,
                 history: list[dict] | None = None,
                 use_worktree: bool = True,
                 qc_fn=None, sleep_fn=time.sleep,
                 ledger_path: Path | None = None,
                 audit_fn=None, progress=None, provider_policy=None) -> dict:
    """Execute all planned tasks in dependency order. Returns a report."""
    progress = progress or (lambda **kw: None)
    progress(phase="ADMISSION_AUDIT", state="RUNNING", total=len(plan.tasks), done=0, unit="tasks")
    source_sha = current_base_commit(workspace)
    test_command = (provider_policy or {}).get("test_command") or _test_command(workspace)

    def admission_events(result: dict) -> None:
        for planned in plan.tasks:
            ns_id = namespaced_id(plan, planned.task_id)
            context = _event_context(plan, ns_id, provider_policy, source_sha)
            difficulty = frozen_difficulty(planned, workspace, test_command)
            store.append_event(ns_id, "plan.difficulty_frozen", "RECORDED",
                               hashlib.sha256(json.dumps([ns_id, plan.plan_id, plan.revision,
                                                          difficulty], sort_keys=True).encode()).hexdigest(),
                               difficulty=difficulty, **context)
            event = emit_gate(store, ns_id, "ADMIT", result, context)
            if event["status"] != "PASS":
                gate = event["gate"]
                store.append_event(
                    ns_id, "learning.recorded", "UNPROVEN",
                    hashlib.sha256(f"learning|{event['idempotency_key']}".encode()).hexdigest(),
                    learning=learning_record(
                        problem=f"ADMIT blocked: {gate.get('reason')}",
                        cause=gate.get("global_findings") or [gate.get("detail")],
                        criterion="ADMIT gate_passed with fresh authorized authority snapshot "
                                  "covering all mission requirements"),
                    next_action=gate.get("reason"), **context)
                store.append_event(
                    ns_id, "task.outcome", "SKIPPED",
                    hashlib.sha256(f"outcome|{event['idempotency_key']}".encode()).hexdigest(),
                    detail=gate.get("reason"), next_action="AUTHORITY_OR_SCOPE_RESOLUTION",
                    **context)

    if is_read_only(plan.source_command):
        reason = "READ_ONLY_TOOL_RUNTIME_NOT_RECONCILED"
        admission_events({"gate_passed": False, "reason": reason})
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
        admission_events({"gate_passed": False, "reason": "DUPLICATE_TASK_TARGET"})
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
    admission_events(admission)
    if admission.get("gate_passed") is not True:
        blocked = [{"task_id": t.task_id, "decision": "BLOCKED",
                    "status": "SKIPPED",
                    "reason": admission.get("reason", "AUDIT_GATE_BLOCKED")}
                   for t in plan.tasks]
        return {"plan_id": plan.plan_id, "revision": plan.revision,
                "tasks": blocked, "reused": 0, "done": 0, "failed": 0,
                "skipped": len(blocked), "status": "BLOCKED",
                "admission_audit": admission,
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
                                  audit_fn, plan_scope, progress, provider_policy))
        report["tasks"].append(entry)
        outcome_context = _event_context(plan, entry["ns_id"], provider_policy, source_sha)
        store.append_event(entry["ns_id"], "task.outcome", str(entry.get("status", "UNKNOWN")),
                           hashlib.sha256(json.dumps(
                               [entry["ns_id"], entry.get("status"), entry.get("decision"),
                                entry.get("reason"), entry.get("receipt") or entry.get("reused_receipt"),
                                source_sha], sort_keys=True).encode()).hexdigest(),
                           detail=entry.get("reason") or entry.get("failure_class"),
                           next_action=entry.get("next_action"),
                           receipt_uri=entry.get("receipt") or entry.get("reused_receipt"),
                           **outcome_context)
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
                 ledger_path: Path | None, audit_fn, plan_scope: dict, progress,
                 provider_policy=None) -> dict:
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
    event_context = _event_context(plan, ns_id, provider_policy, base_commit)
    fingerprint = fingerprint_for(planned.goal, workspace, base_commit)
    decision = classify(planned.goal, workspace, base_commit, store,
                        receipts_dir, task_id=ns_id,
                        max_attempts=planned.max_repairs + 1,
                        account_id=(provider_policy or {}).get("account_id"))
    entry: dict = {"decision": decision.decision, "reason": decision.reason,
                   "fingerprint": fingerprint[:16],
                   "evidence_refs": list(decision.evidence_refs)}
    if decision.decision in ("BLOCKED", "MONITOR"):
        entry["status"] = "SKIPPED"
        return entry
    if decision.decision == "REUSE":
        receipt_ref = decision.evidence_refs[0].removeprefix("receipt:")
        previous = read_receipt(receipts_dir / receipt_ref)
        previous_hints = previous["input"].get("hints", {})
        previous_scope = previous_hints.get("approved_scope", {})
        if (previous["mission_id"] != plan.mission_id
                or previous_scope.get("allowed_paths") != allowed_paths
                or previous_hints.get("acceptance") != list(planned.acceptance)
                or previous["input"].get("test_command") != (
                    (provider_policy or {}).get("test_command") or _test_command(workspace))):
            return {**entry, "decision": "BLOCKED", "status": "SKIPPED",
                    "reason": "REUSE_SCOPE_MISMATCH"}
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

    approval = policy_decision("edit", ns_id, workspace, paths=allowed_paths,
                               approved_paths=allowed_paths, isolated=registry is not None)
    if approval["decision"] != "AUTO_APPROVE":
        return {**entry, "status": "SKIPPED", "decision": "BLOCKED",
                "reason": "REQUIRE_HUMAN_APPROVAL", "pending_approval": approval}
    policy = provider_policy or {"provider": "qwen-local"}
    test_command = policy.get("test_command") or _test_command(workspace)
    test_approval = policy_decision("test", ns_id, workspace, command=test_command)
    if test_approval["decision"] != "AUTO_APPROVE":
        return {**entry, "status": "SKIPPED", "decision": "BLOCKED",
                "reason": "REQUIRE_HUMAN_APPROVAL", "pending_approval": test_approval}

    repo = workspace
    if registry is not None:
        try:
            worktree = registry.create(workspace, ns_id)
            repo = worktree["workspace_path"]
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
                  "test_command": test_command,
                  "preferred_provider": policy.get("provider") or "auto",
                  "max_repairs": planned.max_repairs,
                  "hints": {**policy, "isolated_worktree": registry is not None,
                            "base_commit": base_commit, "plan_id": plan.plan_id,
                            "base_workspace_digest": workspace_digest(workspace, allowed_paths),
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
    if decision.decision != "RESUME":
        prior_work = retrieve_work(planned.goal, workspace, base_commit,
                                   store, receipts_dir,
                                   account_id=policy.get("account_id"),
                                   ranking=policy.get("memory_ranking", "baseline"))
        task.evidence.append(approval)
        task.evidence.append(test_approval)
        task.evidence.append({
            "event": "strategy_selected", "strategy": "BOUNDED_EDIT_TEST_REPAIR",
            "summary": "Qwen-first proposal routing; explicit scope; evidence-grounded prior work; mandatory independent QC.",
            "prior_work": [{"provenance": case["provenance"],
                            "state": case["RESULT"]["state"],
                            "verified_for_current_source": case["verified_for_current_source"]}
                           for case in prior_work]})
        if prior_work:
            history.append({"role": "user", "content":
                            "Prior observations are retrieval hints, never authorization or current proof:\n"
                            + json.dumps(prior_work, ensure_ascii=False)[:4000]})
        _, created = store.enqueue(task)
        if not created:
            entry.update({"decision": "MONITOR", "reason": "ALREADY_QUEUED",
                          "status": "SKIPPED"})
            return entry
    else:
        queued_path = store.inbox / f"{ns_id}.json"
        if not queued_path.is_file():
            return {**entry, "status": "SKIPPED", "reason": "RESUME_TASK_UNAVAILABLE"}
        task = Task.from_dict(json.loads(queued_path.read_text(encoding="utf-8")))
        if Path(task.task_input.repo).resolve() != Path(repo).resolve():
            return {**entry, "status": "SKIPPED", "reason": "RESUME_WORKTREE_MISMATCH"}
        if (task.task_input.hints.get("base_commit") != base_commit
                or task.task_input.hints.get("plan_digest") != plan_scope["plan_digest"]):
            return {**entry, "status": "SKIPPED", "reason": "RESUME_SOURCE_OR_PLAN_MISMATCH"}

    chain = (execution_chain(config, task, ledger_path) if provider_policy is not None
             else qwen_chain(config))
    history.append({"role": "user", "content": planned.goal[:2000]})

    def before_mutation(current_task):
        approval = policy_decision(
            "edit", ns_id, current_task.task_input.repo, paths=allowed_paths,
            approved_paths=allowed_paths, isolated=registry is not None)
        current_task.evidence.append(approval)
        if approval["decision"] != "AUTO_APPROVE":
            return {"gate_passed": False, "reason": "REQUIRE_HUMAN_APPROVAL"}
        context = {
            **plan_scope, "task": planned.to_dict(),
            "task_id": planned.task_id, "queue_task_id": ns_id,
            "repo": current_task.task_input.repo, "source_sha": base_commit,
            "allowed_paths": allowed_paths, "fingerprint": fingerprint,
            "queue_store": store, "lease_token": current_task.lease_token,
        }
        mutation = _audit(audit_fn, "BEFORE_MUTATION", context)
        emit_gate(store, ns_id, "BEFORE_MUTATION", mutation, event_context)
        return mutation

    result = None
    while True:
        store.recover()
        claimed = store.claim(ns_id)
        if claimed is None or claimed.task_id != ns_id:
            # Never disturb a foreign claim: it stays leased and the
            # executor reports the conflict honestly.
            entry.update({"status": "SKIPPED", "reason": "TASK_LEASE_OR_COOLDOWN",
                          "next_action": "WAIT_OR_RECONCILE_CHECKPOINT"})
            return entry
        result = run_task(claimed, store, config, ledger_path, chain=chain,
                          history=history,
                          before_mutation_fn=before_mutation, progress=progress)
        if result.status == "DONE":
            break
        if result.status == "BLOCKED":
            store.defer(result)
            receipt = record_receipt(store, receipts_dir, result, event_context, terminal=True)
            return {**entry, "status": "BLOCKED", "next_action": result.next_action,
                    "receipt": str(receipt), "qc_passed": False,
                    "checkpoint": str(store.checkpoints / f"{ns_id}.json")}
        if result.failure_class == "LEASE_LOST" or not store.lease_valid(
                result.task_id, result.lease_token):
            entry.update({"status": "FAILED", "failure_class": "LEASE_LOST",
                          "next_action": "RECONCILE_LEASE"})
            return entry
        outcome = store.fail(result, result.failure_class or "UNKNOWN",
                             result.next_action,
                             retry=(result.failure_class != "POLICY_DENY"
                                    and result.next_action not in {
                                        "STAGNATION_DETECTED", "STAGNATION_NO_SOURCE_DIFF",
                                        "ESCALATE_AFTER_REPAIRS"}))
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
            emit_gate(store, ns_id, "AFTER_TEST", audit_after, event_context)
            if audit_after.get("gate_passed") is not True:
                result.status = "FAILED"
                result.failure_class = "POLICY_DENY"
                result.next_action = "AUDIT_ESCALATE"
                store.fail(result, result.failure_class, result.next_action,
                           retry=False)
                receipt = record_receipt(store, receipts_dir, result, event_context, terminal=True)
                entry.update({"status": result.status,
                              "failure_class": result.failure_class,
                              "next_action": result.next_action,
                              "receipt": str(receipt)})
                return entry

            result.status = "QC_PENDING"
            request_dir = state_dir / "verifier_handoff"
            candidate_receipt = record_receipt(store, receipts_dir, result, event_context)
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
            store.append_event(
                ns_id, "verifier.independent_proof",
                "AUTHENTICATED" if isinstance(proof, dict) and proof.get("authenticated") else "BLOCKED",
                hashlib.sha256(f"proof|{ns_id}|{patch_digest}|{bool(proof)}".encode()).hexdigest(),
                detail=None if proof else result.evidence[-1].get("reason"), **event_context)

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
            store.append_event(
                ns_id, "qc.checked", "PASS" if qc_report.get("passed") is True else "FAIL",
                hashlib.sha256(json.dumps([ns_id, patch_digest, qc_report.get("passed"),
                                           qc_report.get("independent_verified")],
                                          sort_keys=True).encode()).hexdigest(),
                detail={"independent_verified": qc_report.get("independent_verified"),
                        "failed_checks": [c.get("name") for c in qc_report.get("checks", [])
                                          if isinstance(c, dict) and c.get("passed") is False]},
                **event_context)
            result.status = "QC_PENDING"
            result.next_action = "BEFORE_CLOSE_AUDIT"
            close_receipt = record_receipt(store, receipts_dir, result, event_context)
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
            emit_gate(store, ns_id, "BEFORE_CLOSE", audit_close, event_context)
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
                result.output["time_to_verified_fix_ms"] = int(
                    (time.time() - datetime.fromisoformat(
                        result.created_at).timestamp()) * 1000)
                receipt = record_receipt(store, receipts_dir, result, event_context, terminal=True)
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
        receipt = record_receipt(store, receipts_dir, result, event_context, terminal=True)
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
    if (probe / "coding_worker_v2" / "tests").is_dir():
        return "python3 -m unittest discover -s coding_worker_v2/tests"
    return "python3 -m unittest"
