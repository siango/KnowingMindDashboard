"""`km` — KM Intelligent Coding Agent entry (subcommands, non-interactive).

plan   NL command -> validated execution plan (preview + save)
run    classify -> execute -> QC a saved plan (worktree isolated)
status unified monitor view (queue, receipts, last run, QC)
tools  discover registry tools / call one tool (destructive needs --yes)
qc     run QC over a completed queue task / read back a receipt
resume recover expired leases after restart
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE.parent))

from coding_worker_v2.agent import executor as agent_exec  # noqa: E402
from coding_worker_v2.agent import memory as agent_memory  # noqa: E402
from coding_worker_v2.agent import monitor as agent_monitor  # noqa: E402
from coding_worker_v2.agent import planner as agent_planner  # noqa: E402
from coding_worker_v2.agent import qc as agent_qc  # noqa: E402
from coding_worker_v2.agent import tools as agent_tools  # noqa: E402
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.pipeline import inspect_repo, list_files  # noqa: E402
from coding_worker_v2.providers import (ProviderError,  # noqa: E402
                                        generate_with_fallback)
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.receipts import read_receipt  # noqa: E402
from coding_worker_v2.tasks import Task  # noqa: E402

MISSION_DEFAULT = "KM-INTELLIGENT-CODING-AGENT-V3"


def _ctx(args) -> dict:
    config = WorkerConfig.from_env()
    base = Path(args.base) if args.base else BASE
    store = QueueStore(base / "queue")
    receipts = base / "receipts"
    state = base / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "plans").mkdir(exist_ok=True)
    return {"config": config, "store": store, "receipts_dir": receipts,
            "state_dir": state, "workspace": args.workspace or str(Path.cwd()),
            "owner_node": config.owner_node, "mission_id": MISSION_DEFAULT}


def cmd_plan(args, ctx) -> int:
    workspace = ctx["workspace"]
    repo_info = inspect_repo(workspace)
    registry = agent_tools.build_default_registry()
    allowed = registry.names()
    history: list[dict] = []
    plan_id = args.plan_id or f"p{time.strftime('%Y%m%dT%H%M%S')}"
    revision = 1
    if args.revise:
        prior = json.loads(Path(args.revise).read_text(encoding="utf-8"))
        plan_id = prior.get("plan_id", plan_id)
        revision = int(prior.get("revision", 1)) + 1
        history = [{"role": "assistant", "content": json.dumps(prior)[:4000]}]
        if args.feedback:
            history.append({"role": "user", "content": args.feedback[:2000]})
    command = args.command
    if args.revise and not command:
        command = prior.get("source_command", "")
    plan = None
    engine = "qwen-local"
    if not args.simple:
        chain = agent_exec.qwen_chain(ctx["config"])
        if chain:
            try:
                gen, _ = generate_with_fallback(
                    agent_planner.plan_messages(command, repo_info, allowed,
                                                history,
                                                list_files(workspace)),
                    chain, max_tokens=2048, timeout=ctx["config"].task_timeout_s)
                data = agent_planner.parse_plan(gen.text)
                # CLI-owned identity wins over model output (deterministic).
                data["mission_id"] = args.mission or MISSION_DEFAULT
                data["plan_id"] = plan_id
                data["owner_node"] = ctx["owner_node"]
                data["source_command"] = command
                data["revision"] = revision
                plan = agent_planner.validate_plan_dict(data, allowed)
            except (ProviderError, agent_planner.PlanError) as exc:
                print(json.dumps({"warning": f"model plan failed: {exc}",
                                  "fallback": "deterministic"}))
    if plan is None:
        engine = "deterministic-fallback"
        plan = agent_planner.simple_plan(
            command, args.mission or MISSION_DEFAULT, plan_id, ctx["owner_node"])
        plan.revision = revision
        plan.engine = engine
    path = ctx["state_dir"] / "plans" / f"{plan.plan_id}.json"
    path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(agent_planner.preview(plan))
    print(f"saved: {path}")
    return 0


def cmd_run(args, ctx) -> int:
    plan_path = Path(args.plan)
    if not plan_path.is_file():
        plan_path = ctx["state_dir"] / "plans" / f"{args.plan}.json"
    registry = agent_tools.build_default_registry()
    plan = agent_planner.validate_plan_dict(
        json.loads(plan_path.read_text(encoding="utf-8")), registry.names())

    def qc_fn(result, qctx):
        full = {"config": ctx["config"], "store": ctx["store"],
                "workspace": ctx["workspace"], **qctx}
        return agent_qc.run_qc(result, full)

    report = agent_exec.execute_plan(
        plan, ctx["config"], ctx["store"], ctx["receipts_dir"],
        ctx["state_dir"], ctx["workspace"],
        use_worktree=not args.no_worktree, qc_fn=qc_fn,
        ledger_path=BASE / "state" / "spend_ledger.json")
    last = ctx["state_dir"] / "agent_last.json"
    last.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("---")
    print(json.dumps(agent_monitor.agent_status({
        "mission_id": plan.mission_id, "owner_node": ctx["owner_node"],
        "store": ctx["store"], "receipts_dir": ctx["receipts_dir"],
        "progress": {"done": report["done"] + report["reused"],
                     "total": len(report["tasks"])},
        "active_model": ctx["config"].qwen_model or "qwen-auto",
        "current_step": "RUN_COMPLETE", "qc_status": report["status"],
        "next_action": report["next_action"],
        "evidence": [t.get("receipt", "") for t in report["tasks"] if t.get("receipt")]},
    ), ensure_ascii=False, indent=2))
    return 0 if report["status"] == "DONE" else 1


def cmd_status(args, ctx) -> int:
    _ = args
    last_path = ctx["state_dir"] / "agent_last.json"
    progress: dict = {}
    qc_status, next_action = "NOT_RUN", "AWAIT_COMMAND"
    evidence: list = []
    if last_path.is_file():
        try:
            last = json.loads(last_path.read_text(encoding="utf-8"))
            progress = {"done": last.get("done", 0) + last.get("reused", 0),
                        "total": len(last.get("tasks", []))}
            qc_status = last.get("status", "")
            next_action = last.get("next_action", "")
            evidence = [t.get("receipt", "") for t in last.get("tasks", [])
                        if t.get("receipt")]
        except ValueError:
            pass
    print(json.dumps(agent_monitor.agent_status({
        "mission_id": ctx["mission_id"], "owner_node": ctx["owner_node"],
        "store": ctx["store"], "receipts_dir": ctx["receipts_dir"],
        "progress": progress, "active_model": ctx["config"].qwen_model or "qwen-auto",
        "current_step": "STATUS", "qc_status": qc_status,
        "next_action": next_action, "evidence": evidence}), ensure_ascii=False, indent=2))
    return 0


def cmd_tools(args, ctx) -> int:
    registry = agent_tools.build_default_registry()
    tool_ctx = {"workspace": ctx["workspace"], "store": ctx["store"],
                "receipts_dir": str(ctx["receipts_dir"]),
                "config": ctx["config"], "evidence": [],
                "authorize_destructive": bool(args.yes)}
    if not args.call:
        print(json.dumps(registry.discover(tool_ctx), ensure_ascii=False, indent=2))
        return 0
    try:
        result = registry.call(args.call, json.loads(args.args or "{}"), tool_ctx)
    except agent_tools.ToolError as exc:
        print(json.dumps({"ok": False, "reason": exc.reason, "detail": exc.detail}))
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False)[:8000])
    return 0


def cmd_qc(args, ctx) -> int:
    if args.receipt:
        data = read_receipt(Path(args.receipt))
        print(json.dumps({"ok": True, "status": data["status"],
                          "task_id": data["task_id"]}))
        return 0
    task_path = ctx["store"].done / args.done / "task.json"
    task = Task.from_dict(json.loads(task_path.read_text(encoding="utf-8")))
    repo = task.task_input.repo
    report = agent_qc.run_qc(task, {"config": ctx["config"], "store": ctx["store"],
                                    "workspace": ctx["workspace"], "repo": repo})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def cmd_resume(args, ctx) -> int:
    _ = args
    result = ctx["store"].recover()
    print(json.dumps({"recovered": result,
                      "queue": ctx["store"].health()}, ensure_ascii=False))
    return 0


def cmd_fingerprint(args, ctx) -> int:
    _ = ctx
    base = agent_memory.current_base_commit(args.repo)
    print(json.dumps({
        "fingerprint": agent_memory.fingerprint_for(args.goal, args.repo, base),
        "base_commit": base}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="km", description="KM Coding Agent V3")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--base", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan", help="NL command -> plan")
    plan.add_argument("command", nargs="?", default="")
    plan.add_argument("--mission", default="")
    plan.add_argument("--plan-id", default="")
    plan.add_argument("--simple", action="store_true")
    plan.add_argument("--revise", default="")
    plan.add_argument("--feedback", default="")
    plan.set_defaults(func=cmd_plan)
    run = sub.add_parser("run", help="execute a saved plan")
    run.add_argument("plan")
    run.add_argument("--no-worktree", action="store_true")
    run.set_defaults(func=cmd_run)
    status = sub.add_parser("status", help="monitor view")
    status.set_defaults(func=cmd_status)
    tools = sub.add_parser("tools", help="tool discovery / call")
    tools.add_argument("--call", default="")
    tools.add_argument("--args", default="{}")
    tools.add_argument("--yes", action="store_true")
    tools.set_defaults(func=cmd_tools)
    qc = sub.add_parser("qc", help="QC a done task / read a receipt")
    qc.add_argument("--done", default="")
    qc.add_argument("--receipt", default="")
    qc.set_defaults(func=cmd_qc)
    resume = sub.add_parser("resume", help="recover after restart")
    resume.set_defaults(func=cmd_resume)
    fp = sub.add_parser("fingerprint", help="task fingerprint")
    fp.add_argument("--repo", required=True)
    fp.add_argument("--goal", required=True)
    fp.set_defaults(func=cmd_fingerprint)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = _ctx(args)
    try:
        return args.func(args, ctx)
    except (ProviderError, agent_planner.PlanError, agent_tools.ToolError,
            agent_exec.AgentError, agent_qc.QCError, FileNotFoundError,
            ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
