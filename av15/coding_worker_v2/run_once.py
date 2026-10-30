"""Phase 2/3 orchestrator: run one queued task end to end.

Usage:
  python3 -m coding_worker_v2.run_once --once            # recover + run one task
  python3 -m coding_worker_v2.run_once --enqueue task.json
  python3 -m coding_worker_v2.run_once --health
  python3 -m coding_worker_v2.run_once --discover
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))

from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.pipeline import (PipelineError, bounded_edit,  # noqa: E402
                                       coding_messages, inspect_repo, list_files,
                                       make_patch, parse_edits, run_tests,
                                       syntax_check)
from coding_worker_v2.providers import (ProviderError, discover,  # noqa: E402
                                        generate_with_fallback, route_chain)
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.receipts import write_receipt  # noqa: E402
from coding_worker_v2.tasks import Task  # noqa: E402


def run_task(task: Task, store: QueueStore, config: WorkerConfig,
             ledger_path: Path | None = None,
             chain: list | None = None,
             history: list[dict] | None = None) -> Task:
    """Execute one task. Optional chain/history are V3 agent injections.

    chain=None preserves V2 routing; history=None preserves V2 prompts.
    """
    started = time.monotonic()
    repo_info = inspect_repo(task.task_input.repo)
    task.evidence.append({"event": "repo_inspected", "repo": repo_info})
    store.save_checkpoint(task.task_id, "inspected", {"repo": repo_info})
    if not repo_info["exists"]:
        return _fail(task, "REPO_FAIL", "repo path missing")
    repo_files = list_files(task.task_input.repo)

    if chain is None:
        chain = route_chain(config, task.task_input.preferred_provider, ledger_path)
    task.evidence.append({"event": "route_selected",
                          "chain": [p.name for p in chain]})
    if not chain:
        return _fail(task, "PROVIDER_FAIL", "NO_PROVIDER_AVAILABLE")

    clipped_history = [m for m in (history or [])
                       if isinstance(m, dict) and m.get("role") in ("user", "assistant")][-20:]

    failure = ""
    repairs_used = 0
    max_repairs = min(task.task_input.max_repairs, config.max_repair_attempts)
    for round_no in range(max_repairs + 1):
        if not store.heartbeat(task.task_id):
            return _fail(task, "LEASE_LOST", "RECONCILE_LEASE")
        task.status = "REPAIRING" if round_no else "RUNNING"
        base_prompt = coding_messages(task.task_input.goal, repo_info, failure,
                                        files=repo_files)
        prompt = ([base_prompt[0]] + clipped_history + base_prompt[1:]
                  if clipped_history else base_prompt)
        try:
            gen, trace = generate_with_fallback(
                prompt, chain, max_tokens=2048, timeout=config.task_timeout_s)
        except ProviderError as exc:
            failure_class = "BUDGET_EXCEEDED" if "BUDGET" in exc.reason else "PROVIDER_FAIL"
            return _fail(task, failure_class, f"RETRY_OR_ESCALATE {exc.reason}")
        task.evidence.append({"event": "model_generated",
                              "provider": gen.provider, "model": gen.model,
                              "version": gen.version, "latency_ms": gen.latency_ms,
                              "tokens_in": gen.tokens_in, "tokens_out": gen.tokens_out,
                              "history_turns": len(clipped_history),
                              "route": [{"provider": t.provider, "model": t.model,
                                         "ok": t.ok, "reason": t.reason} for t in trace]})
        try:
            edits = parse_edits(gen.text)
            changed = bounded_edit(task.task_input.repo, edits, config)
        except PipelineError as exc:
            failure = f"{exc.failure_class}: {exc.detail}"
            repairs_used = round_no + 1
            task.evidence.append({"event": "edit_failed", "failure": failure})
            continue
        store.save_checkpoint(task.task_id, "edited", {"changed": changed})
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
            continue
        try:
            test_result = run_tests(task.task_input.repo, task.task_input.test_command,
                                    config.test_timeout_s)
        except PipelineError as exc:
            return _fail(task, exc.failure_class, f"FIX_{exc.failure_class}")
        task.evidence.append({"event": "tests_run", "tests": {
            "ran": test_result["ran"], "rc": test_result["rc"],
            "duration_ms": test_result["duration_ms"],
            "output_tail": test_result["output"][-3000:]}})
        if test_result["ran"] and test_result["rc"] != 0:
            failure = f"TEST_FAIL rc={test_result['rc']}: {test_result['output'][-1500:]}"
            repairs_used = round_no + 1
            continue
        # Success.
        task.output = {"changed": changed, "patch_chars": len(patch),
                       "provider": gen.provider, "model": gen.model,
                       "repairs_used": repairs_used,
                       "duration_ms": int((time.monotonic() - started) * 1000)}
        task.status = "DONE"
        task.next_action = "REVIEW_PATCH"
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
        recovered = store.recover()
        if recovered["recovered"]:
            print(json.dumps({"recovered": recovered}))
        task = store.claim()
        if task is None:
            print(json.dumps({"idle": True, "queue": store.health()}))
            return 0
        result = run_task(task, store, config, ledger)
        run_status = result.status
        if run_status == "DONE":
            store.complete(result)
        else:
            outcome = store.fail(result, result.failure_class, result.next_action)
            # Queue file keeps its requeue state; the run record keeps FAILED.
            result.status = run_status
            result.evidence.append({"event": "queue_outcome", "outcome": outcome})
        receipt = write_receipt(BASE / "receipts", result)
        print(json.dumps({"task_id": result.task_id, "status": result.status,
                          "failure_class": result.failure_class,
                          "next_action": result.next_action,
                          "receipt": str(receipt)}))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
