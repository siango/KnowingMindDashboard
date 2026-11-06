"""V3 Phase 6: monitoring view. Reads existing state only.

Aggregates queue, leases, receipts, plan progress, QC and CI into one
status object. No scheduler, no new SSOT.
"""
from __future__ import annotations

import time
import json
import subprocess
from pathlib import Path

from .memory import (_iter_receipts, _iter_terminal_archives, terminal_conflicts,
                     receipt_verified, current_base_commit, work_case,
                     strategy_effectiveness)
from ..providers import ProviderResources, ProviderError
import hashlib


def execution_observations(task: dict, checkpoint=None) -> dict:
    events = [e for e in task.get("evidence", []) if isinstance(e, dict)]
    output = task.get("output") or {}
    hints = task.get("input", {}).get("hints", {})
    tests = [e.get("tests", {}) for e in events if e.get("event") == "tests_run"]
    generations = [e for e in events if e.get("event") == "model_generated"]
    def measured(key):
        values = [g[key] for g in generations if type(g.get(key)) in (int, float)]
        return {"known_total": sum(values) if values else None,
                "observations": len(values), "complete": len(values) == len(generations)
                and bool(generations)}
    return {
        "task_id": task.get("task_id"), "owner_node": task.get("owner_node"),
        "source_sha": task.get("source_sha") or hints.get("base_commit"),
        "provider": output.get("provider"), "model": output.get("model"),
        "changed_files": output.get("changed", (checkpoint or {}).get("data", {}).get("changed", [])),
        "tests": {"passed_runs": sum(t.get("ran") is True and t.get("rc") == 0 for t in tests),
                  "failed_runs": sum(t.get("ran") is True and t.get("rc") not in (0, None) for t in tests),
                  "skipped_runs": sum(t.get("ran") is not True for t in tests)},
        "usage": {key: measured(key) for key in ("tokens_in", "tokens_out",
                                               "actual_cost_usd", "estimated_cost_usd")},
        "latency_ms": [g.get("latency_ms") for g in generations],
        "checkpoint": checkpoint, "current_step": (checkpoint or {}).get("stage"),
        "retry_count": max(0, task.get("attempts", 0) - 1),
        "recovery": [e for e in events if e.get("event") in
                     {"lease_reconciled", "checkpoint_resumed"}],
        "pending_approval": [e for e in events if e.get("event") == "approval_decision"
                             and e.get("decision") == "REQUIRE_HUMAN_APPROVAL"
                             and not e.get("human_authorized")],
        "decision_summary": [e for e in events if e.get("event") in
                             {"route_selected", "strategy_selected", "provider_switch", "provider_paused"}],
        "next_action": task.get("next_action"),
        "qc_status": "VERIFIED_PASS" if receipt_verified(task) else task.get("status"),
        "time_to_verified_fix_ms": output.get("time_to_verified_fix_ms")
        if receipt_verified(task) else None,
    }


def agent_status(ctx: dict) -> dict:
    """Build the agent status view. Never raises on missing pieces."""
    store = ctx.get("store")
    receipts_dir = ctx.get("receipts_dir")
    queue = {}
    if store is not None:
        try:
            queue = store.health()
        except Exception:
            queue = {"error": "queue unreadable"}
    receipts_count, last_receipt = 0, ""
    if receipts_dir is not None:
        try:
            files = sorted(Path(receipts_dir).glob("*.json"),
                           key=lambda p: p.stat().st_mtime)
            receipts_count = len(files)
            last_receipt = files[-1].name if files else ""
        except OSError:
            pass
    progress = ctx.get("progress", {}) or {}
    total = int(progress.get("total", 0) or 0)
    done = int(progress.get("done", 0) or 0)
    percent = round(done / total * 100, 1) if total else 0.0
    evidence = ctx.get("evidence", []) or []
    if not isinstance(evidence, list):
        evidence = []
    conflicts = []
    known_receipts = set()
    task_views, cases = [], []
    if store is not None and receipts_dir is not None:
        try:
            for receipt_path, receipt in _iter_receipts(Path(receipts_dir)) or ():
                if receipt is None:
                    conflicts.append({"reason": "RECEIPT_HISTORY_UNREADABLE",
                                      "receipt_ref": str(receipt_path)})
                else:
                    known_receipts.add(receipt.get("task_id"))
                    conflicts.extend(terminal_conflicts(store, receipt))
                    task_views.append(execution_observations(receipt))
                    cases.append(work_case(receipt, str(receipt_path),
                                           hashlib.sha256(receipt_path.read_bytes()).hexdigest()))
        except OSError:
            conflicts.append({"reason": "TERMINAL_RECONCILIATION_READ_FAILED"})
    if store is not None:
        try:
            for folder_name, path, archived in _iter_terminal_archives(store):
                if archived is None:
                    conflicts.append({"reason": "TERMINAL_RECORD_UNREADABLE",
                                      "queue_ref": f"queue:{folder_name}/{path.parent.name}"})
                elif archived.get("task_id") not in known_receipts:
                    conflicts.append({"reason": "TERMINAL_RECEIPT_MISSING",
                                      "queue_ref": f"queue:{folder_name}/{path.parent.name}"})
        except OSError:
            conflicts.append({"reason": "TERMINAL_RECONCILIATION_READ_FAILED"})
    view = {
        "MISSION": ctx.get("mission_id", "KM-INTELLIGENT-CODING-AGENT-V3"),
        "TASK": ctx.get("active_task", ""),
        "OWNER_NODE": ctx.get("owner_node", "AV15"),
        "PROGRESS_PERCENT": percent,
        "ACTIVE_MODEL": ctx.get("active_model", ""),
        "ACTIVE_TOOL": ctx.get("active_tool", ""),
        "CURRENT_STEP": ctx.get("current_step", "IDLE"),
        "QC_STATUS": ctx.get("qc_status", "NOT_RUN"),
        "RETRY_COUNT": int(ctx.get("retry_count", 0) or 0),
        "EVIDENCE": [str(e)[:200] for e in evidence][-10:],
        "NEXT_ACTION": ctx.get("next_action", "AWAIT_COMMAND"),
        "queue": queue,
        "receipts_count": receipts_count,
        "last_receipt": last_receipt,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if conflicts:
        view["PROGRESS_PERCENT"] = None
        view["QC_STATUS"] = "BLOCKED"
        view["NEXT_ACTION"] = "RECONCILE_QUEUE_RECEIPT_CONFLICT"
    view["reconciliation_conflicts"] = conflicts
    active = []
    if store is not None:
        for folder in (store.doing, store.inbox):
            for path in sorted(folder.glob("*.json")):
                if path.name.endswith(".lease.json"):
                    continue
                try:
                    task = json.loads(path.read_text())
                    checkpoint = store.load_checkpoint(task["task_id"])
                    observation = execution_observations(task, checkpoint)
                    repo = task.get("input", {}).get("repo", "")
                    observation["source_matches"] = current_base_commit(repo) == observation["source_sha"]
                    if observation["source_matches"] and observation["changed_files"]:
                        diff = subprocess.run(
                            ["git", "-C", repo, "diff", "--numstat", "--", *observation["changed_files"]],
                            capture_output=True, text=True, timeout=15, check=False)
                        observation["diff_numstat"] = diff.stdout[:8000] if diff.returncode == 0 else None
                    active.append(observation)
                except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
                    active.append({"task_id": path.stem, "error": type(exc).__name__})
        try:
            view["provider_resources"] = ProviderResources(store).read()
        except ProviderError as exc:
            view["provider_resources"] = {"error": exc.reason}
            view["QC_STATUS"] = "BLOCKED"
            view["NEXT_ACTION"] = "RECONCILE_PROVIDER_RESOURCE_STATE"
        audit = store.load_checkpoint(".tool-audit")
        view["tool_policy_events"] = (audit or {}).get("data", {}).get("events", [])[-20:]
    verified = [v for v in task_views if v["qc_status"] == "VERIFIED_PASS"]
    failed = [v for v in task_views if v["qc_status"] == "FAILED"]
    resumed = [v for v in task_views if v["recovery"]]
    verified_costs = [v["usage"]["actual_cost_usd"]["known_total"] for v in verified
                      if v["usage"]["actual_cost_usd"]["complete"]]
    view.update(active_tasks=active, task_history=task_views[-20:], learning_memory=cases[-10:],
                metrics={"verified_completion_rate": len(verified) / len(task_views) if task_views else None,
                         "cost_per_verified_task": sum(verified_costs) / len(verified)
                         if verified and len(verified_costs) == len(verified) else None,
                         "resume_success_rate": sum(v["qc_status"] == "VERIFIED_PASS" for v in resumed)
                         / len(resumed) if resumed else None,
                         "failed_tasks": len(failed)})
    patterns = {}
    for case in cases:
        failure = case["PROBLEM"]["failure_class"]
        if failure:
            row = patterns.setdefault(failure, {"count": 0, "original_refs": []})
            row["count"] += 1
            row["original_refs"].append(case["provenance"]["original_ref"])
    view["failure_patterns"] = patterns
    view["metrics"]["repeated_failure_rate"] = (
        sum(row["count"] - 1 for row in patterns.values())
        / sum(row["count"] for row in patterns.values()) if patterns else None)
    if store is not None and receipts_dir is not None:
        view["strategy_effectiveness"] = strategy_effectiveness(store, Path(receipts_dir))
    state_dir = ctx.get("state_dir")
    view["acceptance"] = {key: {"status": "NOT_VERIFIED",
                                "reason": "CURRENT_SOURCE_ACCEPTANCE_NOT_RECORDED"}
                          for key in ("A1", "A2", "A3", "A4", "A5")}
    if state_dir:
        observations = sorted((Path(state_dir) / "multi_ai").glob("unblock_acceptance*.json"))
        if observations:
            try:
                observation = json.loads(observations[-1].read_text())
                if observation.get("source", {}).get("runtime_source_sha") == current_base_commit(
                        str(Path(__file__).parent)):
                    view["acceptance"] = observation.get("acceptance", view["acceptance"])
                else:
                    view["acceptance_evidence_rejected"] = "SOURCE_SHA_MISMATCH"
            except (OSError, ValueError):
                view["acceptance_evidence_rejected"] = "UNREADABLE"
    if active:
        view["TASK"] = active[0].get("task_id", "")
        view["CURRENT_STEP"] = active[0].get("current_step") or view["CURRENT_STEP"]
    return view
