"""Phase 2/3 orchestrator: run one queued task end to end.

Usage:
  python3 -m coding_worker_v2.run_once --once            # blocked legacy bypass
  python3 -m coding_worker_v2.run_once --enqueue task.json
  python3 -m coding_worker_v2.run_once --health
  python3 -m coding_worker_v2.run_once --discover
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))

from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.agent.sandbox import scrubbed_env, policy_decision  # noqa: E402
from coding_worker_v2.agent.memory import current_base_commit, workspace_digest  # noqa: E402
from coding_worker_v2.agent.auditor import SCOPE_DIGEST  # noqa: E402
from coding_worker_v2.pipeline import (PipelineError, bounded_edit,  # noqa: E402
                                       coding_messages, inspect_repo, list_files,
                                       make_patch, parse_edits, run_tests,
                                       syntax_check)
from coding_worker_v2.providers import (ProviderError, ProviderResources, discover,  # noqa: E402
                                        generate_with_fallback, route_chain)
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.receipts import write_receipt  # noqa: E402
from coding_worker_v2.tasks import Task  # noqa: E402


def run_task(task: Task, store: QueueStore, config: WorkerConfig,
             ledger_path: Path | None = None,
             chain: list | None = None,
             history: list[dict] | None = None,
             before_mutation_fn=None, progress=None) -> Task:
    with store.keepalive(task) as lease_lost:
        result = _run_task_inner(task, store, config, ledger_path, chain,
                                 history, lease_lost, before_mutation_fn, progress)
    if lease_lost.is_set() and result.status == "DONE":
        return _fail(result, "LEASE_LOST", "RECONCILE_LEASE")
    return result


def _run_task_inner(task: Task, store: QueueStore, config: WorkerConfig,
                    ledger_path: Path | None, chain: list | None,
                    history: list[dict] | None, lease_lost,
                    before_mutation_fn=None, progress=None) -> Task:
    """Execute one task. Optional chain/history are V3 agent injections.

    chain=None preserves V2 routing; history=None preserves V2 prompts.
    """
    progress = progress or (lambda **kw: None)
    progress(phase="INSPECT", state="RUNNING", tool="repo_inspect", model="-")
    started = time.monotonic()
    if not task.lease_token or not store.heartbeat(task.task_id, task.lease_token):
        return _fail(task, "LEASE_LOST", "RECONCILE_LEASE")
    repo_info = inspect_repo(task.task_input.repo)
    task.evidence.append({"event": "repo_inspected", "repo": repo_info})
    if not repo_info["exists"]:
        return _fail(task, "REPO_FAIL", "repo path missing")
    repo_files = list_files(task.task_input.repo)
    scope = (task.task_input.hints or {}).get("approved_scope")
    if not isinstance(scope, dict):
        return _fail(task, "POLICY_DENY", "APPROVED_SCOPE_MISSING")
    allowed_paths = scope.get("allowed_paths")
    if (not isinstance(allowed_paths, list) or not allowed_paths
            or any(not isinstance(path, str) for path in allowed_paths)
            or len(set(allowed_paths)) != len(allowed_paths)):
        return _fail(task, "POLICY_DENY",
                     "APPROVED_FILE_ALLOWLIST_REQUIRED")
    scope_valid = (
        scope.get("mission_id") == task.mission_id
        and scope.get("queue_task_id") == task.task_id
        and scope.get("owner_node") == task.owner_node
        and scope.get("goal") == task.task_input.goal
        and scope.get("plan_id") == (task.task_input.hints or {}).get("plan_id")
        and scope.get("plan_digest")
        == (task.task_input.hints or {}).get("plan_digest")
        and scope.get("scope_digest") == SCOPE_DIGEST
        and scope.get("acceptance")
        == (task.task_input.hints or {}).get("acceptance")
        and scope.get("base_commit")
        == (task.task_input.hints or {}).get("base_commit")
        and isinstance(scope.get("revision"), int)
        and scope.get("revision", 0) >= 1
        and isinstance(scope.get("source_command"), str)
        and bool(scope.get("source_command").strip())
        and scope.get("allowed_paths") == allowed_paths)
    if not scope_valid:
        return _fail(task, "POLICY_DENY", "APPROVED_SCOPE_MISMATCH")
    unapproved = [path for path in allowed_paths if path not in repo_files]
    if unapproved:
        return _fail(task, "POLICY_DENY",
                     f"APPROVED_PATH_NOT_FOUND:{unapproved[0][:100]}")

    source_sha = current_base_commit(task.task_input.repo)
    if scope.get("base_commit") and scope["base_commit"] != source_sha:
        return _fail(task, "POLICY_DENY", "SOURCE_SHA_MISMATCH")
    saved = store.load_checkpoint(task.task_id)
    if saved is None and (store.checkpoints / f"{task.task_id}.json").exists():
        return _fail(task, "POLICY_DENY", "CHECKPOINT_UNREADABLE")
    resume = (saved or {}).get("data", {})
    phase = resume.get("phase", "inspected")
    try:
        digest = workspace_digest(task.task_input.repo, allowed_paths)
    except (OSError, ValueError):
        return _fail(task, "POLICY_DENY", "CHECKPOINT_WORKSPACE_UNREADABLE")
    if saved:
        snapshot = resume.get("task_snapshot", {})
        if (resume.get("source_sha") != source_sha
                or snapshot.get("task_id") != task.task_id
                or snapshot.get("mission_id") != task.mission_id
                or snapshot.get("owner_node") != task.owner_node
                or snapshot.get("input") != task.to_dict()["input"]):
            return _fail(task, "POLICY_DENY", "CHECKPOINT_SOURCE_OR_SCOPE_MISMATCH")
        if resume.get("workspace_digest") != digest:
            return _fail(task, "POLICY_DENY", "CHECKPOINT_WORKSPACE_MISMATCH")
        recovered = [e for e in task.evidence if e.get("event") == "lease_reconciled"]
        task.evidence = list(snapshot.get("evidence", []))
        task.evidence.extend(e for e in recovered if e not in task.evidence)
        task.output = dict(snapshot.get("output") or {})
        task.evidence.append({"event": "checkpoint_resumed", "stage": saved["stage"],
                              "source_sha": source_sha, "lease_validated": True})

    def checkpoint(stage, **data):
        task.evidence.append(policy_decision(
            "checkpoint", task.task_id, task.task_input.repo))
        store.checkpoint_task(task, stage, {
            "source_sha": source_sha,
            "workspace_digest": workspace_digest(task.task_input.repo, allowed_paths),
            **data})
        progress(checkpoint=stage, retry=task.attempts - 1)

    inspection = policy_decision("inspect", task.task_id, task.task_input.repo,
                                 paths=allowed_paths)
    task.evidence.append(inspection)
    if inspection["decision"] != "AUTO_APPROVE":
        checkpoint("waiting_approval", phase=phase)
        task.status, task.next_action = "BLOCKED", "REQUIRE_HUMAN_APPROVAL"
        return task
    if not saved:
        checkpoint("inspected", phase="inspected")

    if chain is None:
        chain = route_chain(config, task.task_input.preferred_provider, ledger_path)
    task.evidence.append({"event": "route_selected",
                          "chain": [p.name for p in chain]})
    if not chain:
        return _fail(task, "PROVIDER_FAIL", "NO_PROVIDER_AVAILABLE")

    clipped_history = [m for m in (history or [])
                       if isinstance(m, dict) and m.get("role") in ("user", "assistant")][-20:]

    resources = ProviderResources(store)
    failure = resume.get("failure", "")
    repairs_used = resume.get("repair_round", 0)
    max_repairs = min(task.task_input.max_repairs, config.max_repair_attempts)
    seen_failures = list(resume.get("seen_failures", []))
    changed = list(resume.get("changed", []))
    for round_no in range(repairs_used, max_repairs + 1):
        if not store.heartbeat(task.task_id, task.lease_token):
            return _fail(task, "LEASE_LOST", "RECONCILE_LEASE")
        task.status = "REPAIRING" if round_no else "RUNNING"
        source_texts = {}
        total_bytes = 0
        try:
            for rel in allowed_paths:
                raw = (Path(task.task_input.repo) / rel).read_bytes()
                total_bytes += len(raw)
                if total_bytes > config.max_bytes_per_task:
                    return _fail(task, "POLICY_DENY", "SOURCE_CONTEXT_BUDGET_EXCEEDED")
                source_texts[rel] = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return _fail(task, "REPO_FAIL", "SOURCE_CONTEXT_UNREADABLE")
        base_prompt = coding_messages(task.task_input.goal, repo_info, failure,
                                      files=allowed_paths, sources=source_texts)
        prompt = ([base_prompt[0]] + clipped_history + base_prompt[1:]
                  if clipped_history else base_prompt)
        edits = resume.get("pending_edits") if phase == "generated" else None
        if phase not in {"generated", "edited", "tested"}:
            try:
                gen, trace = generate_with_fallback(
                    prompt, chain, max_tokens=2048, timeout=config.task_timeout_s,
                    resources=resources, task=task, progress=progress)
            except ProviderError as exc:
                task.evidence.append({"event": "provider_paused", "reason": exc.reason,
                                      "detail": exc.detail,
                                      "route": [vars(t) for t in exc.trace]})
                gates = [resources.gate(p.name) for p in chain]
                deadlines = [g[2] for g in gates if g[2] is not None]
                retry_at = min(deadlines) if deadlines else None
                task.status, task.next_action = "BLOCKED", "WAIT_PROVIDER_RESOURCE"
                progress(phase="PROVIDER_PAUSED", state="BLOCKED",
                         quota="UNKNOWN", reason=exc.detail or exc.reason)
                checkpoint("waiting_provider", phase="inspected", retry_at=retry_at,
                           failure=failure, repair_round=round_no)
                return task
            previous = task.output.get("provider")
            if previous and previous != gen.provider:
                task.evidence.append({"event": "provider_switch", "from": previous,
                                      "to": gen.provider, "reason": "RESOURCE_OR_REPAIR_ROUTE",
                                      "task_id": task.task_id, "source_sha": source_sha})
            task.output.update(provider=gen.provider, model=gen.model)
            task.evidence.append({"event": "model_generated",
                                  "provider": gen.provider, "model": gen.model,
                                  "version": gen.version, "latency_ms": gen.latency_ms,
                                  "tokens_in": gen.tokens_in, "tokens_out": gen.tokens_out,
                                  "actual_cost_usd": gen.execution.get("actual_cost_usd"),
                                  "estimated_cost_usd": gen.execution.get("estimated_cost_usd"),
                                  "history_turns": len(clipped_history),
                                  "route": [vars(t) for t in trace]})
            try:
                edits = parse_edits(gen.text)
            except PipelineError as exc:
                failure = f"{exc.failure_class}: {exc.detail}"
                repairs_used = round_no + 1
                state = workspace_digest(task.task_input.repo, allowed_paths) + failure
                seen_failures.append(hashlib.sha256(state.encode()).hexdigest())
                task.evidence.append({"event": "edit_failed", "failure": failure})
                if seen_failures.count(seen_failures[-1]) >= 2:
                    return _fail(task, "PARSE_FAIL", "STAGNATION_DETECTED")
                checkpoint("repairing", phase="inspected", seen_failures=seen_failures,
                           failure=failure, repair_round=round_no + 1)
                continue
            checkpoint("generated", phase="generated", pending_edits=edits,
                       repair_round=round_no)
            phase = "generated"
        if lease_lost.is_set() or not store.lease_valid(
                task.task_id, task.lease_token):
            return _fail(task, "LEASE_LOST", "RECONCILE_LEASE")
        progress(phase="BEFORE_MUTATION_AUDIT", state="RUNNING",
                 model=task.output.get("model", "-"), timeout=None)
        try:
            if phase == "generated" and before_mutation_fn is not None:
                audit = before_mutation_fn(task)
                task.evidence.append({"event": "mission_audit",
                                      "stage": "BEFORE_MUTATION",
                                      "result": audit})
                if not isinstance(audit, dict) or audit.get("gate_passed") is not True:
                    return _fail(task, "POLICY_DENY", "AUDIT_GATE_BLOCKED")
            if phase == "generated":
                checkpoint("mutation_pending", phase="generated", pending_edits=edits)
                changed = sorted(set(changed) | set(bounded_edit(
                    task.task_input.repo, edits, config, allowed_paths=allowed_paths)))
        except PipelineError as exc:
            failure = f"{exc.failure_class}: {exc.detail}"
            repairs_used = round_no + 1
            task.evidence.append({"event": "edit_failed", "failure": failure})
            state = workspace_digest(task.task_input.repo, allowed_paths) + failure
            seen_failures.append(hashlib.sha256(state.encode()).hexdigest())
            if seen_failures.count(seen_failures[-1]) >= 2:
                return _fail(task, exc.failure_class, "STAGNATION_DETECTED")
            phase = "inspected"
            checkpoint("repairing", phase=phase, seen_failures=seen_failures,
                       failure=failure, repair_round=round_no + 1)
            continue
        checkpoint("edited", phase="edited", changed=changed)
        try:
            patch = make_patch(task.task_input.repo, changed, config)
        except PipelineError as exc:
            patch = ""
            task.evidence.append({"event": "patch_note", "failure": exc.failure_class})
        syntax = syntax_check(task.task_input.repo, changed)
        task.evidence.append({"event": "syntax_checked", "syntax": syntax,
                              "patch_chars": len(patch), "changed": changed})
        if not syntax["ok"]:
            failure = "SYNTAX_FAIL: " + "; ".join(syntax["errors"])
            repairs_used = round_no + 1
            phase = "inspected"
            state = workspace_digest(task.task_input.repo, allowed_paths) + failure
            seen_failures.append(hashlib.sha256(state.encode()).hexdigest())
            if seen_failures.count(seen_failures[-1]) >= 2:
                return _fail(task, "SYNTAX_FAIL", "STAGNATION_DETECTED")
            checkpoint("repairing", phase=phase, failure=failure,
                       seen_failures=seen_failures, repair_round=round_no + 1)
            continue
        try:
            approval = policy_decision("test", task.task_id, task.task_input.repo,
                                       command=task.task_input.test_command)
            task.evidence.append(approval)
            progress(approval=approval["decision"])
            if approval["decision"] != "AUTO_APPROVE":
                task.status, task.next_action = "BLOCKED", "REQUIRE_HUMAN_APPROVAL"
                checkpoint("waiting_approval", phase="edited", changed=changed)
                return task
            progress(phase="TEST", state="RUNNING", tool="test_run")
            test_result = run_tests(
                task.task_input.repo, task.task_input.test_command,
                config.test_timeout_s, env=scrubbed_env())
        except PipelineError as exc:
            return _fail(task, exc.failure_class, f"FIX_{exc.failure_class}")
        task.evidence.append({"event": "tests_run", "tests": {
            "ran": test_result["ran"], "rc": test_result["rc"],
            "duration_ms": test_result["duration_ms"],
            "output_tail": test_result["output"][-3000:]}})
        if test_result["ran"] and test_result["rc"] != 0:
            failure = f"TEST_FAIL rc={test_result['rc']}: {test_result['output'][-1500:]}"
            repairs_used = round_no + 1
            phase = "inspected"
            state = workspace_digest(task.task_input.repo, allowed_paths) + failure
            seen_failures.append(hashlib.sha256(state.encode()).hexdigest())
            if seen_failures.count(seen_failures[-1]) >= 2:
                return _fail(task, "TEST_FAIL", "STAGNATION_DETECTED")
            checkpoint("repairing", phase=phase, failure=failure,
                       seen_failures=seen_failures, repair_round=round_no + 1)
            continue
        if not patch.strip():
            return _fail(task, "PATCH_FAIL", "STAGNATION_NO_SOURCE_DIFF")
        # Success.
        task.output = {"changed": changed, "patch_chars": len(patch),
                       "patch_digest": hashlib.sha256(
                           patch.encode("utf-8")).hexdigest(),
                       "provider": task.output.get("provider"),
                       "model": task.output.get("model"),
                       "repairs_used": repairs_used,
                       "duration_ms": int((time.monotonic() - started) * 1000)}
        task.status = "DONE"
        task.next_action = "REVIEW_PATCH"
        checkpoint("tested", phase="tested", changed=changed)
        return task
    task.output = {"repairs_used": repairs_used,
                   "duration_ms": int((time.monotonic() - started) * 1000)}
    final_class = "UNKNOWN"
    for candidate in ("TEST_FAIL", "SYNTAX_FAIL", "PARSE_FAIL", "PATCH_FAIL",
                      "POLICY_DENY", "PROVIDER_FAIL"):
        if failure.startswith(candidate):
            final_class = candidate
            break
    return _fail(task, final_class, "ESCALATE_AFTER_REPAIRS")


def _fail(task: Task, failure_class: str, next_action: str) -> Task:
    task.failure_class = failure_class
    task.next_action = next_action
    task.status = "FAILED"
    return task


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KM Coding Worker V2 runner")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--enqueue", metavar="TASK_JSON")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--base", default=str(BASE / "queue"))
    args = parser.parse_args(argv)

    config = WorkerConfig.from_env()
    store = QueueStore(Path(args.base))
    ledger = BASE / "state" / "spend_ledger.json"

    if args.health:
        print(json.dumps({"ok": True, "queue": store.health(),
                          "owner": config.owner_node}))
        return 0
    if args.discover:
        print(json.dumps(discover(config, ledger), indent=2))
        return 0
    if args.enqueue:
        task = Task.from_dict(json.loads(Path(args.enqueue).read_text(encoding="utf-8")))
        task_id, created = store.enqueue(task)
        print(json.dumps({"task_id": task_id, "created": created}))
        return 0
    if args.once:
        print(json.dumps({
            "ok": False,
            "error": "LEGACY_EXECUTION_BLOCKED",
            "detail": "V2 --once bypasses mandatory V3 audit and independent QC; "
                      "use km_main plan/run",
        }))
        return 2
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
