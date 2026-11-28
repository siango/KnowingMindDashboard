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
import re
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


def plan_readiness(plan, store, receipts_dir, workspace, *, account_id=None):
    """Memory-first read projection; does not authorize ADMIT or dispatch."""
    source = current_base_commit(workspace)
    saved = store.load_checkpoint(f".pilot-{plan.plan_id}") or {}
    prior = saved.get("data", {}).get("report") or {}
    nodes = []
    for task in order_tasks(plan):
        cases = retrieve_work(task.goal, workspace, source, store, receipts_dir,
                              account_id=account_id)
        decision = classify(task.goal, workspace, source, store, receipts_dir,
                            task_id=namespaced_id(plan, task.task_id),
                            account_id=account_id)
        contract = task.execution_contract
        io_input = None
        if contract and "io_checks" in contract:
            from .qc import evaluate_task_io
            io_input = evaluate_task_io(task, workspace, phase="input")
        reason = decision.reason
        if prior.get("status") == "BLOCKED":
            reason = "RETAIN_BLOCKED_CHECKPOINT"
        elif contract and contract["source_sha"] != source:
            reason = "TASK_CONTRACT_SOURCE_CHANGED"
        elif io_input and io_input["passed"] is False:
            reason = "TASK_INPUT_CONTRACT_REJECTED"
        elif decision.decision not in {"MONITOR", "BLOCKED"}:
            reason = "ADMIT_AND_INDEPENDENT_QC_REQUIRED"
        nodes.append({"task_id": task.task_id, "ns_id": namespaced_id(plan, task.task_id),
                      "deps": list(task.deps), "owner_node": plan.owner_node,
                      "source_sha": source,
                      "task_scope_digest": (contract or {}).get("scope_digest"),
                      "decision": decision.decision, "reason": reason,
                      "evidence_refs": list(decision.evidence_refs),
                      "memory_cases": cases, "dispatch_authorized": False,
                      **({"io_input": io_input} if io_input else {}),
                      "qc": "NOT_RUN", "cost": "UNKNOWN"})
    return {"plan_id": plan.plan_id, "mission_id": plan.mission_id,
            "nodes": nodes, "execution_mode": "SEQUENTIAL",
            "concurrency_authorized": False, "dispatch_authorized": False,
            "central_sync": "UNSYNCED"}


def execute_plan(plan: Plan, config: WorkerConfig, store: QueueStore,
                 receipts_dir: Path, state_dir: Path, workspace: str,
                 history: list[dict] | None = None,
                 use_worktree: bool = True,
                 qc_fn=None, sleep_fn=time.sleep,
                 ledger_path: Path | None = None,
                 audit_fn=None, progress=None, provider_policy=None,
                 selected_task_ids=None) -> dict:
    """Execute all planned tasks in dependency order. Returns a report."""
    progress = progress or (lambda **kw: None)
    progress(phase="ADMISSION_AUDIT", state="RUNNING", total=len(plan.tasks), done=0, unit="tasks")
    source_sha = current_base_commit(workspace)
    selected = {t.task_id for t in plan.tasks}
    if selected_task_ids is not None:
        if (not isinstance(selected_task_ids, (list, tuple)) or not selected_task_ids
                or not set(selected_task_ids).issubset(selected)):
            raise AgentError("INVALID_TASK_SELECTION")
        selected = set(selected_task_ids)
        # Audit the original complete DAG; a tick cannot rewrite its scope or
        # trust a caller-supplied list of completed dependencies.
        for task in plan.tasks:
            if task.task_id in selected:
                for dep in task.deps:
                    if dep not in selected and not completion_readback(
                            plan, dep, store, receipts_dir, workspace,
                            account_id=(provider_policy or {}).get("account_id"))["verified"]:
                        raise AgentError("DEPENDENCY_RECEIPT_REQUIRED", dep)
    test_command = (provider_policy or {}).get("test_command") or _test_command(workspace)
    if plan.tasks:
        from .planner import validate_plan_dict
        if any(task.execution_contract for task in plan.tasks):
            plan = validate_plan_dict(plan.to_dict())
        readiness = plan_readiness(plan, store, receipts_dir, workspace,
                                   account_id=(provider_policy or {}).get("account_id"))
        held = [node for node in readiness["nodes"] if node["task_id"] in selected and (node["reason"] in {
            "RETAIN_BLOCKED_CHECKPOINT", "TASK_CONTRACT_SOURCE_CHANGED", "TASK_INPUT_CONTRACT_REJECTED"}
            or node["decision"] == "MONITOR"
            or (node["decision"] == "BLOCKED" and
                any(t.execution_contract for t in plan.tasks)))]
        if held:
            return {"plan_id": plan.plan_id, "revision": plan.revision,
                    "status": "BLOCKED", "done": 0, "reused": 0, "failed": 0,
                    "skipped": len(plan.tasks), "tasks": readiness["nodes"],
                    "readiness": readiness, "next_action": held[0]["reason"]}

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
                "next_action": admission.get("reason", "AUDIT_GATE_BLOCKED")}
    history = history if history is not None else []
    ordered = order_tasks(plan)
    registry = WorktreeRegistry(state_dir) if use_worktree else None
    report: dict = {"plan_id": plan.plan_id, "revision": plan.revision,
                    "tasks": [], "reused": 0, "done": 0, "failed": 0,
                    "skipped": 0}
    failed_ids: set[str] = set()
    for planned in ordered:
        if planned.task_id not in selected:
            report["tasks"].append({"task_id": planned.task_id,
                                    "status": "DEFERRED", "reason": "OUTSIDE_BOUNDED_TICK"})
            report["skipped"] += 1
            continue
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


def completion_readback(plan, task_id, store, receipts_dir, workspace, *, account_id=None):
    """Exact task/source/scope readback; similarity and executor output are not QC."""
    from .memory import receipt_verified, terminal_conflicts, _iter_receipts
    from .verification import read_signed_proof
    from datetime import timezone
    planned = next(t for t in plan.tasks if t.task_id == task_id)
    path = Path(receipts_dir) / f"{namespaced_id(plan, task_id)}.json"
    result = {"verified": False, "receipt": str(path), "acceptance_passed": 0,
              "acceptance_total": len(planned.acceptance)}
    if not path.is_file():
        return result
    try:
        receipt = read_receipt(path)
        hints = receipt["input"].get("hints", {})
        source = current_base_commit(workspace)
        scope = hints.get("approved_scope", {})
        valid = (receipt["task_id"] == namespaced_id(plan, task_id)
                 and receipt["mission_id"] == plan.mission_id
                 and receipt["owner_node"] == plan.owner_node
                 and receipt.get("source_sha") == source
                 and hints.get("plan_id") == plan.plan_id
                 and hints.get("base_commit") == source
                 and hints.get("acceptance") == planned.acceptance
                 and scope.get("allowed_paths") == planned.allowed_paths
                 and (account_id is None or hints.get("account_id") == account_id)
                 and hints.get("task_execution_contract") == planned.execution_contract
                 and hints.get("plan_digest") == hashlib.sha256(json.dumps(
                     plan.to_dict(), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode()).hexdigest()
                 and receipt_verified(receipt)
                 and not terminal_conflicts(store, receipt))
        if valid:
            # Root pointer alone cannot override corrupt or divergent history.
            latest = [r for _, r in _iter_receipts(Path(receipts_dir))
                      if r and r.get("task_id") == receipt["task_id"]]
            valid = len(latest) == 1 and latest[0] == receipt
        if valid:
            handoff = Path(os.environ.get("KMW2_STATE_DIR", "")) / "verifier_handoff"
            embedded = [e.get("proof", {}) for e in receipt["evidence"]
                        if isinstance(e, dict) and e.get("event") == "independent_verification"]
            version = embedded[-1].get("receipt_version") if embedded else None
            valid = type(version) is int and version > 0
            if valid:
                request = handoff / f"{receipt['task_id']}.v{version:06d}.request.json"
                proof = read_signed_proof(request, request.with_name(request.name.replace(
                    ".request.json", ".proof.json")),
                    Path(os.environ.get("KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY", "")),
                    verified_at=datetime.fromtimestamp(receipt["recorded_at"], timezone.utc))
                version = proof.get("receipt_version")
                candidate = (Path(receipts_dir) / "history" / receipt["task_id"] /
                             f"v{version:06d}.json") if type(version) is int and version > 0 else None
                candidate_data = json.loads(candidate.read_bytes()) if candidate and candidate.is_file() else {}
                valid = (candidate is not None and candidate.is_file()
                         and hashlib.sha256(candidate.read_bytes()).hexdigest() == proof.get("receipt_sha256")
                         and proof.get("task_id") == receipt["task_id"]
                         and proof.get("mission_id") == plan.mission_id
                         and proof.get("plan_id") == plan.plan_id
                         and proof.get("plan_digest") == hints.get("plan_digest")
                         and proof.get("allowed_paths") == planned.allowed_paths
                         and proof.get("executor_id") == plan.owner_node
                         and proof.get("scope_digest") == receipt.get("scope_digest") == hints.get("scope_digest") == SCOPE_DIGEST
                         and proof.get("source_sha") == source
                         and proof.get("patch_digest") == receipt["output"].get("patch_digest")
                         and proof.get("lease_token_sha256") == receipt.get("lease_token_sha256")
                         and candidate_data.get("output") == {
                             k: v for k, v in receipt["output"].items() if k != "time_to_verified_fix_ms"}
                         and any(e.get("proof") == proof for e in receipt["evidence"]
                                 if isinstance(e, dict) and e.get("event") == "independent_verification"))
        result.update(verified=bool(valid), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        # A whole-task PASS does not prove individual prose acceptance criteria.
        # Only explicit criterion readbacks in the independently verified receipt count.
        checks = receipt.get("output", {}).get("acceptance_checks", [])
        if valid and isinstance(checks, list):
            passed = {c.get("criterion") for c in checks if isinstance(c, dict)
                      and isinstance(c.get("criterion"), str)
                      and c.get("passed") is True and c.get("evidence_ref")}
            result["acceptance_passed"] = sum(c in passed for c in planned.acceptance)
    except (OSError, ValueError, KeyError, TypeError, VerificationError):
        result["reason"] = "RECEIPT_READBACK_INVALID"
    return result


def reconcile_once(plan, ctx):
    """One bounded tick for the existing supervisor/pilot, never a scheduler.

    READ -> RECONCILE -> PLAN -> ELIGIBILITY -> CLAIM -> EXECUTE -> QC ->
    RECORD -> NEXT. execute_plan retains all audit, QueueStore lease, independent
    QC and receipt gates. Trusted capability inputs come from middleware only.
    """
    import fcntl
    from . import memory, planner
    from ..providers import ProviderResources, ProviderError
    plan = planner.validate_plan_dict(plan.to_dict())
    store = ctx["store"]
    if plan.owner_node != ctx["config"].owner_node or plan.mission_id != ctx["mission_id"]:
        raise AgentError("CONTROLLER_IDENTITY_MISMATCH")
    permitted = ctx.get('execution_task_ids')
    if permitted is not None and (not isinstance(permitted, (list,tuple)) or not permitted
            or any(not isinstance(t, str) for t in permitted)
            or not set(permitted).issubset({namespaced_id(plan,t.task_id) for t in plan.tasks})):
        raise AgentError('CONTROLLER_TASK_AUTHORITY_SCOPE_INVALID')
    key = f".reconcile-{plan.plan_id}"
    # Serialize ticks across processes, without holding the QueueStore lock
    # during execution (enqueue/claim/heartbeat own that lock themselves).
    with (store.checkpoints / f"{key}.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "MONITOR", "reason": "CONTROLLER_TICK_ACTIVE", "dispatch": False}
        prior = store.load_checkpoint(key)
        if prior is None and (store.checkpoints / f"{key}.json").exists():
            raise AgentError("CONTROLLER_CHECKPOINT_UNREADABLE")
        saved = (prior or {}).get("data", {})
        if not isinstance(saved, dict):
            raise AgentError("CONTROLLER_CHECKPOINT_UNREADABLE")
        if saved and (saved.get("mission_id") != plan.mission_id
                      or saved.get("account_id") != ctx.get("account_id")
                      or saved.get("plan_id") != plan.plan_id):
            raise AgentError("CONTROLLER_CHECKPOINT_IDENTITY_MISMATCH")
        records = saved.get("tasks", {})
        if not isinstance(records, dict) or any(not isinstance(v, dict) for v in records.values()):
            raise AgentError("CONTROLLER_CHECKPOINT_UNREADABLE")
        gui_evidence = None
        gui_binding = ctx.get("gui_binding", {})
        gui_task = gui_binding.get("task_id")
        if gui_binding and gui_task in {namespaced_id(plan, t.task_id) for t in plan.tasks}:
            if permitted is None or gui_task in permitted:
                from .capabilities import ingest_gui
                from .tools import ToolError
                try:
                    gui_evidence = ingest_gui(ctx, gui_task)
                except (ToolError, OSError, ValueError) as exc:
                    gui_evidence = {"blocked": getattr(exc, "reason", type(exc).__name__)}
        readiness = plan_readiness(plan, store, ctx["receipts_dir"], ctx["workspace"],
                                   account_id=ctx.get("account_id"))
        proofs = {t.task_id: completion_readback(
            plan, t.task_id, store, ctx["receipts_dir"], ctx["workspace"],
            account_id=ctx.get("account_id")) for t in plan.tasks}
        total = sum(p["acceptance_total"] for p in proofs.values())
        passed = sum(p["acceptance_passed"] for p in proofs.values())
        result = {"mission_id": plan.mission_id, "plan_id": plan.plan_id,
                  "status": "BLOCKED", "tasks": [], "dispatch": False,
                  "kmnw": "PREVIEW_ONLY", "central_sync": "UNSYNCED",
                  "acceptance": {"passed": passed, "total": total,
                                 "percent": round(100 * passed / total, 2) if total else None},
                  "receipts": proofs, "next_action": "RESOLVE_RECORDED_BLOCKER"}
        result["gui_evidence"] = gui_evidence
        result["node_routing"] = planner.node_route_preview(
            plan, ctx.get("kmnw_candidates", []), readiness=readiness, now=time.time(),
            completed_task_ids=[t for t, proof in proofs.items() if proof["verified"]])
        control = store.load_checkpoint(f".control-{plan.plan_id}") or {}
        if control.get("data", {}).get("paused") is True:
            result.update(reason="OWNER_PAUSED", next_action="SCOPED_OWNER_RESUME")
            return result
        now = time.time()
        source = current_base_commit(ctx["workspace"])

        def persist():
            with store._locked():
                store.save_checkpoint(key, "controller_tick", {
                    "mission_id": plan.mission_id, "account_id": ctx.get("account_id"),
                    "plan_id": plan.plan_id, "source_sha": source,
                    "tasks": records, "report": result})

        def digest(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True,
                separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

        def file_identity(path):
            if not path:
                return None
            p = Path(path)
            return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and p.stat().st_size <= 1_000_000 else "UNAVAILABLE"

        for task in order_tasks(plan):
            node = next(n for n in readiness["nodes"] if n["task_id"] == task.task_id)
            row = {"task_id": task.task_id, "canonical_task_id": namespaced_id(plan, task.task_id),
                   "deps": task.deps, "status": "BLOCKED", "notify_owner": False}
            result["tasks"].append(row)
            if permitted is not None and namespaced_id(plan, task.task_id) not in permitted:
                row.update(status='DEFERRED', reason='OUTSIDE_SCOPED_EXECUTION')
                continue
            ns_id = namespaced_id(plan, task.task_id)
            current_context = memory.execution_context(ctx, source, task.allowed_paths, ns_id)
            prior_record = records.get(task.task_id, {})
            signature = prior_record.get('error_signature')
            if not isinstance(signature, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', signature):
                signature = ''
            lessons = memory.planning_lessons(task.goal+' '+signature, ctx["workspace"], source,
                store, ctx["receipts_dir"], account_id=ctx.get("account_id"), plan=plan,
                context=current_context)
            row["memory"] = lessons
            row['method_selection'] = planner.select_method(lessons, current_context)
            row['memory_error_signature'] = signature or None
            row['execution_context'] = current_context
            if proofs[task.task_id]["verified"]:
                row.update(status="DONE", reason="VERIFIED_RECEIPT_READBACK")
                continue
            if any(not proofs[d]["verified"] for d in task.deps):
                row.update(reason="WAIT_DEPENDENCIES")
                continue
            ns_id = namespaced_id(plan, task.task_id)
            feed = ctx.get("capability_inputs", {}).get(ns_id, {})
            source_paths = {p: file_identity(Path(ctx["workspace"]) / p)
                            for p in task.allowed_paths}
            evidence = {"plan": plan.to_dict(), "source": source, "paths": source_paths,
                        "checkpoint": store.load_checkpoint(ns_id),
                        "memory_decision": [node["decision"], node["reason"], node["evidence_refs"]],
                        "receipt": proofs[task.task_id], "config": ctx.get("pilot"),
                        "authority": {name: file_identity(os.environ.get(name)) for name in (
                            "KMW2_MISSION_AUDITOR_SNAPSHOT", "KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY")},
                        "auditor": file_identity(Path(os.environ.get("KMW2_MISSION_AUDITOR_ROOT", "/nonexistent")) /
                                                  "scripts/control/km_mission_auditor.py"),
                        "manifest": file_identity(Path(os.environ.get("KMW2_MISSION_AUDITOR_ROOT", "/nonexistent")) /
                                                  "docs/control/KM_UNIFIED_MISSION_ACCEPTANCE_V1.json"),
                        "provider_resources": store.load_checkpoint(ProviderResources.KEY),
                        "feed": {k: (hashlib.sha256(v).hexdigest() if isinstance(v, bytes) else v)
                                 for k, v in feed.items()}}
            fingerprint = digest(evidence)
            old = records.get(task.task_id, {})
            comparison = memory.compare_context(old.get('execution_context'), current_context)
            row['context_comparison'] = comparison
            row['chosen_method'] = ('REVALIDATE_CHANGED_CONTEXT' if comparison['state'] == 'DIFFERENT'
                else 'REUSE_MATCHING_CONTEXT' if comparison['state'] == 'MATCH'
                else 'INSPECT_EXISTING_EVIDENCE')
            count = old.get("attempts", 0)
            row["attempts"] = count
            from .self_development import tested_checkpoint_recoverable
            recovering_tested = (node['decision'] == 'RESUME' and tested_checkpoint_recoverable(
                plan, task, ctx, store.load_checkpoint(ns_id)))
            if recovering_tested:
                row['recovery'] = 'VERIFIED_TESTED_CHECKPOINT_NO_NEW_REPAIR_ATTEMPT'
            if count >= min(3, task.max_repairs + 1) and not recovering_tested:
                row.update(reason="RETRY_BUDGET_EXHAUSTED")
                continue
            if (old.get("fingerprint") == fingerprint
                    and comparison['state'] != 'DIFFERENT' and not recovering_tested):
                row.update(reason=old.get("reason", "RECONCILE_INTERRUPTED_TICK"),
                           unchanged_blocker=True, chosen_method='DEFER_UNCHANGED_FAILURE')
                continue
            if not recovering_tested and old.get("retryable") is True and now < old.get("retry_at", 0):
                row.update(reason="RETRY_BACKOFF")
                continue

            def blocked(reason):
                row.update(reason=reason, notify_owner=old.get("fingerprint") != fingerprint
                           or old.get("reason") != reason,
                           diagnosis=learning_record(problem=reason,
                               cause=[{"evidence_fingerprint": fingerprint, "observed_gate": reason,
                                       "context_comparison": comparison,
                                       "chosen_method": row['chosen_method']}],
                               criterion="Matching authorized gate evidence and independent receipt readback"))
                records[task.task_id] = {"fingerprint": fingerprint, "reason": reason,
                                         "attempts": count, "retryable": False,
                                         "error_signature": reason,
                                         "execution_context": current_context}
                records[task.task_id]['method_selection'] = row['method_selection']
                records[task.task_id]['method_result'] = row.get('method_result')
                persist()

            if node["decision"] in {"MONITOR", "BLOCKED"} or node["reason"] in {
                    "RETAIN_BLOCKED_CHECKPOINT", "TASK_CONTRACT_SOURCE_CHANGED", "TASK_INPUT_CONTRACT_REJECTED"}:
                blocked(node["reason"])
                continue
            if task.execution_contract is None:
                blocked("TASK_EXECUTION_CONTRACT_REQUIRED")
                continue
            if row['method_selection']['action'] == 'INSPECT_EXISTING_SCOPE':
                from .self_development import inspect as inspect_existing
                try:
                    inspection = inspect_existing(plan, task.task_id, ctx)
                    row['method_result'] = {'status': 'INSPECTED_EXISTING_SOURCE',
                        'source_sha': inspection['source_sha'],
                        'source_files': inspection['source_files'],
                        'tool_contract_sha256': inspection['tool_contract_sha256'],
                        'dispatch': False, 'mutation': False}
                except (ValueError, OSError) as exc:
                    row['method_result'] = {'status': 'FAILED', 'reason': type(exc).__name__}
                    blocked('LEARNED_METHOD_SCOPE_INSPECTION_FAILED')
                    continue
            if ctx["pilot"]["provider"] != "qwen-local":
                blocked("SAVED_PROVIDER_REQUIRES_RECONCILIATION")
                continue
            scope = {"mission_id": plan.mission_id, "plan_id": plan.plan_id,
                     "revision": plan.revision, "owner_node": plan.owner_node,
                     "source_command": plan.source_command, "workspace": ctx["workspace"],
                     "source_sha": source, "scope_digest": SCOPE_DIGEST,
                     "plan_snapshot": plan.to_dict(), "tasks": [t.to_dict() for t in plan.tasks],
                     "plan_digest": digest(plan.to_dict())}
            admission = _audit(run_mission_audit, "ADMIT", scope)
            if admission.get("gate_passed") is not True:
                blocked(admission.get("reason", "ADMIT_BLOCKED"))
                continue
            try:
                route = ProviderResources(store).select_authenticated_advisory(
                    feed.get("raw"), binding=feed.get("binding"), authentication=feed.get("authentication"),
                    capability="bounded-edit", context_tokens=feed.get("context_tokens", 0),
                    required_permissions=["scoped-edit"])
                binding = route["task_binding"]
                if any(binding.get(k) != v for k, v in {
                        "mission_id": plan.mission_id, "task_id": ns_id,
                        "owner_node": plan.owner_node, "source_sha": source,
                        "scope_digest": task.execution_contract["scope_digest"]}.items()):
                    raise ProviderError("CONTROLLER_CAPABILITY_SCOPE_MISMATCH")
                row["provider_routing"] = route
                expected_route = 'deterministic' if task.execution_contract.get('self_development') else 'qwen-local'
                if route.get("selected_provider") != expected_route:
                    blocked("NO_QUALIFIED_LOCAL_EXECUTOR_ROUTE")
                    continue
                candidates = json.loads(feed["raw"])["candidates"]
                if not any(c.get("provider") == expected_route and c.get("node_id") == plan.owner_node
                           for c in candidates):
                    blocked("LOCAL_EXECUTOR_NODE_BINDING_MISMATCH")
                    continue
            except ProviderError as exc:
                blocked(exc.reason)
                continue
            # Record intent before dispatch. A crash retains the same canonical
            # task and cannot silently clear an unfinished attempt on restart.
            attempt = count if recovering_tested else count + 1
            records[task.task_id] = {"fingerprint": fingerprint, "reason": "RECONCILE_INTERRUPTED_TICK",
                                     "attempts": attempt, "retryable": False,
                                     "method_selection": row['method_selection'],
                                     "method_result": row.get('method_result'),
                                     "execution_context": current_context}
            persist()
            policy = {"provider": expected_route, "account_id": ctx.get("account_id"),
                      "method_selection": row['method_selection'],
                      "context_paths": list(ctx['pilot']['context_paths']),
                      "test_command": ctx["pilot"]["test_command"], "max_provider_dispatches": 1,
                      "external_authorized": False, "memory_ranking": "qc"}
            report = execute_plan(plan, ctx["config"], store, ctx["receipts_dir"],
                ctx["state_dir"], ctx["workspace"], provider_policy=policy,
                selected_task_ids=[task.task_id])
            proof = completion_readback(plan, task.task_id, store, ctx["receipts_dir"],
                                        ctx["workspace"], account_id=ctx.get("account_id"))
            outcome = next((t for t in report.get("tasks", []) if t.get("task_id") == task.task_id), {})
            retryable = outcome.get("failure_class") in {"PROVIDER_FAIL", "TIMEOUT"}
            reason = ("VERIFIED_RECEIPT_READBACK" if proof["verified"] else
                      outcome.get("next_action") or outcome.get("reason") or "QC_OR_RECEIPT_READBACK_REQUIRED")
            row.update(status="DONE" if proof["verified"] else "BLOCKED", reason=reason,
                       attempts=attempt, notify_owner=True, execution_report=report)
            result["dispatch"] = True
            result["receipts"][task.task_id] = proof
            records[task.task_id] = {"fingerprint": fingerprint, "reason": reason,
                                     "method_selection": row['method_selection'],
                                     "method_result": row.get('method_result'),
                                     "error_signature": outcome.get('failure_class') or reason,
                                     "execution_context": current_context,
                                     "attempts": attempt, "retryable": retryable,
                                     "retry_at": now + backoff_s(attempt)}
            # One task per bounded supervisor tick; next tick re-reads authority.
            break
        if all(result["receipts"][t.task_id]["verified"] for t in plan.tasks):
            confirmed = sum(p["acceptance_passed"] for p in result["receipts"].values())
            result.update(status="DONE" if confirmed == total else "QC",
                          next_action="REVIEW_INDEPENDENT_RECEIPTS" if confirmed == total else
                                      "ACCEPTANCE_CRITERIA_READBACK_REQUIRED")
        elif result["dispatch"]:
            result.update(status="PARTIAL", next_action="RECONCILE_NEXT_ELIGIBLE_TASK")
        result["acceptance"]["passed"] = sum(p["acceptance_passed"] for p in result["receipts"].values())
        result["acceptance"]["percent"] = round(100 * result["acceptance"]["passed"] / total, 2) if total else None
        persist()
        return result


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
    self_repair = (planned.execution_contract or {}).get('self_development')
    if self_repair and registry is None:
        return {'decision': 'BLOCKED', 'status': 'SKIPPED', 'reason': 'SELF_REPAIR_ISOLATED_CANDIDATE_REQUIRED'}
    if self_repair and not set(self_repair['source_files']).issubset(
            set(allowed_paths) | set((provider_policy or {}).get('context_paths', []))):
        return {'decision': 'BLOCKED', 'status': 'SKIPPED', 'reason': 'SELF_REPAIR_SNAPSHOT_SCOPE_DENIED'}
    if not allowed_paths:
        return {"decision": "BLOCKED", "status": "SKIPPED",
                "reason": "APPROVED_FILE_ALLOWLIST_REQUIRED"}
    if planned.execution_contract and "io_checks" in planned.execution_contract:
        from .qc import evaluate_task_io
        checked = evaluate_task_io(planned, workspace, phase="input")
        if checked["passed"] is not True:
            return {"decision": "BLOCKED", "status": "SKIPPED",
                    "reason": "TASK_INPUT_CONTRACT_REJECTED", "io_input": checked}
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
                            **({"task_execution_contract": planned.execution_contract}
                               if planned.execution_contract else {}),
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
    if self_repair:
        from .self_development import stage_candidate
        task.task_input.max_repairs = 0
        task.max_attempts = 1
        try:
            stage_candidate(self_repair, task, workspace, registry)
        except ValueError as exc:
            return {**entry, 'status': 'SKIPPED', 'decision': 'BLOCKED', 'reason': str(exc)}
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

    chain = ([] if self_repair else
             execution_chain(config, task, ledger_path) if provider_policy is not None
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

    from . import memory as work_memory
    attempt_scope = {'workspace': task.task_input.repo, 'store': store,
        'receipts_dir': receipts_dir, 'config': config,
        'pilot': {'provider': policy.get('provider'),
                  'test_command': task.task_input.test_command, 'allowed_paths': allowed_paths},
        'account_id': policy.get('account_id'), 'mission_id': plan.mission_id, 'task_id': ns_id}
    result = None
    while True:
        # Existing lease/idempotency gates still decide the claim. Refresh
        # retrieval hints before every claim/retry; memory grants no authority.
        fresh_work = retrieve_work(planned.goal, workspace, base_commit, store, receipts_dir,
                      account_id=policy.get("account_id"),
                      ranking=policy.get("memory_ranking", "baseline"))
        attempt_context = work_memory.execution_context(attempt_scope, base_commit, allowed_paths, ns_id)
        store.recover()
        claimed = store.claim(ns_id)
        if claimed is None or claimed.task_id != ns_id:
            # Never disturb a foreign claim: it stays leased and the
            # executor reports the conflict honestly.
            entry.update({"status": "SKIPPED", "reason": "TASK_LEASE_OR_COOLDOWN",
                          "next_action": "WAIT_OR_RECONCILE_CHECKPOINT"})
            return entry
        # Keep the input bound to the durable checkpoint on recovery. Fresh
        # retrieval context belongs to this attempt's evidence below; replacing
        # the original hint makes an otherwise valid tested resume fail scope
        # comparison after the candidate files have changed.
        claimed.task_input.hints.setdefault('execution_context', attempt_context)
        claimed.evidence.append({'event': 'memory_retrieved',
            'execution_context': attempt_context,
            'chosen_method': 'DETERMINISTIC_APPROVED_EDITS' if self_repair else 'EXISTING_PROVIDER_CHAIN',
            'lessons': [{'provenance': c['provenance'],
                'context_comparison': work_memory.compare_context(c.get('execution_context'), attempt_context),
                'verified_for_current_source': c.get('verified_for_current_source') is True}
                for c in fresh_work], 'authority_granted': False})
        result = run_task(claimed, store, config, ledger_path, chain=chain,
                          history=history, before_mutation_fn=before_mutation, progress=progress,
                          **({'deterministic_edits': self_repair['edits']} if self_repair else {}))
        after_context = work_memory.execution_context(attempt_scope, base_commit, allowed_paths, ns_id)
        context_change = work_memory.compare_context(attempt_context, after_context)
        result.evidence.append({'event': 'attempt_feedback', 'problem': planned.goal,
            'learned_method': policy.get('method_selection'),
            'cause_evidence': {'failure_class': result.failure_class,
                               'context_comparison': context_change},
            'success_criteria': planned.acceptance,
            'chosen_method': 'DETERMINISTIC_APPROVED_EDITS' if self_repair else 'EXISTING_PROVIDER_CHAIN',
            'result': result.status, 'execution_context': attempt_context,
            'reusable_lesson': 'Reuse only matching scoped evidence; revalidate changed context; never infer authority.'})
        if self_repair and result.status == 'DONE':
            from ..pipeline import make_patch
            artifact = state_dir / 'self_development' / f'{ns_id}.patch'
            artifact.parent.mkdir(parents=True, exist_ok=True)
            patch = make_patch(result.task_input.repo, allowed_paths, config)
            artifact.write_text(patch)
            result.output.update(candidate_source_sha=current_base_commit(result.task_input.repo),
                candidate_content_sha256=workspace_digest(result.task_input.repo, list(self_repair['source_files'])),
                rollback_patch=str(artifact), rollback_patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
                proposal_sha256=self_repair['proposal_sha256'], promotion='INDEPENDENT_VERIFIER_REQUIRED')
            store.checkpoint_task(result, 'tested', {'phase': 'tested',
                'source_sha': current_base_commit(result.task_input.repo),
                'workspace_digest': workspace_digest(result.task_input.repo, allowed_paths)})
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
        retry_requested = (result.failure_class != 'POLICY_DENY'
            and result.next_action not in {'STAGNATION_DETECTED', 'STAGNATION_NO_SOURCE_DIFF',
                                           'ESCALATE_AFTER_REPAIRS'})
        if (retry_requested and result.attempts < result.max_attempts
                and context_change['state'] == 'MATCH'):
            result.evidence.append({'event': 'attempt_feedback', 'problem': result.failure_class,
                'cause_evidence': {'context_comparison': context_change},
                'success_criteria': 'Changed authorized input/evidence before another attempt',
                'chosen_method': 'DEFER_UNCHANGED_FAILURE', 'result': 'BLOCKED',
                'execution_context': attempt_context})
            result.next_action = 'UNCHANGED_FAILURE_INPUT'
            store.defer(result)
            receipt = record_receipt(store, receipts_dir, result, event_context, terminal=True)
            return {**entry, 'status': 'BLOCKED', 'next_action': result.next_action,
                    'receipt': str(receipt), 'context_comparison': context_change, 'qc_passed': False}
        outcome = store.fail(result, result.failure_class or "UNKNOWN",
                             result.next_action, retry=retry_requested)
        result.status = "FAILED"
        if outcome == "failed":
            break
        sleep_fn(backoff_s(result.attempts))

    assert result is not None
    qc_report = None
    receipt = None
    if result.status == "DONE":
        if planned.execution_contract and "io_checks" in planned.execution_contract:
            from .qc import evaluate_task_io
            checked = evaluate_task_io(planned, result.task_input.repo, phase="output",
                                       output=result.output, evidence=result.evidence)
            result.evidence.append({"event": "task_io_checked", "result": checked})
            if checked["passed"] is not True:
                result.status, result.failure_class = "FAILED", "POLICY_DENY"
                result.next_action = "TASK_OUTPUT_CONTRACT_REJECTED"
                store.fail(result, result.failure_class, result.next_action, retry=False)
                receipt = record_receipt(store, receipts_dir, result, event_context, terminal=True)
                return {**entry, "status": "FAILED", "next_action": result.next_action,
                        "receipt": str(receipt), "io_output": checked, "qc_passed": False}
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
                if (planned.execution_contract and proof is not None
                        and qc_report.get("passed") is False):
                    from .planner import bounded_repair_task, PlanError
                    failed_checks = [str(c.get("name")) for c in qc_report.get("checks", [])
                                     if c.get("passed") is False and c.get("name")]
                    try:
                        proposal = bounded_repair_task(plan, planned.task_id, failed_checks, 1)
                        result.output["repair_task_proposal"] = proposal
                        entry["repair_task_proposal"] = proposal
                    except PlanError:
                        entry["repair_blocker"] = "BOUNDED_REPAIR_UNAVAILABLE"
                result.evidence.append({"event": "queue_outcome", "status": "BLOCKED",
                                        "next_action": result.next_action})
                store.fail(result, result.failure_class, result.next_action,
                           retry=False)
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
