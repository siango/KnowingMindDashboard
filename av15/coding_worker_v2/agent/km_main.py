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
from coding_worker_v2.agent.progress import LiveProgress
from coding_worker_v2.agent import tools as agent_tools  # noqa: E402
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.pipeline import inspect_repo, list_files  # noqa: E402
from coding_worker_v2.providers import ProviderError  # noqa: E402
from coding_worker_v2.providers import provider_inventory, ProviderResources  # noqa: E402
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
    progress = ctx.get("progress", lambda **kw: None)
    progress(phase="INSPECT", tool="repo_inspect", total=4)
    workspace = ctx["workspace"]
    repo_info = inspect_repo(workspace)
    progress(done=1, tool="-", phase="PREPARE_PLAN")
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
    progress(task=plan_id)
    plan = None
    engine = "qwen-local"
    if not args.simple:
        progress(phase="QWEN_DISCOVERY", state="WAITING",
                 model=ctx["config"].qwen_model or "qwen-auto", timeout=10)
        resources = ProviderResources(ctx["store"])
        provider_ready, reason, retry_at = resources.gate("qwen-local")
        chain = agent_exec.qwen_chain(ctx["config"]) if provider_ready else []
        if not provider_ready:
            print(json.dumps({"warning": "planner provider paused", "reason": reason,
                              "retry_at": retry_at, "fallback": "deterministic"}))
        if chain:
            try:
                progress(phase="QWEN_PLAN", state="WAITING", timeout=ctx["config"].task_timeout_s)
                def generate_plan(messages, max_tokens, timeout):
                    return chain[0].generate_plan(
                        messages, max_tokens=max_tokens, timeout=timeout,
                        response_schema=agent_planner.PLAN_OUTPUT_SCHEMA)
                gen = resources.invoke(
                    chain[0], None,
                    agent_planner.plan_messages(command, repo_info, registry.names(),
                                                history, list_files(workspace)),
                    4096, ctx["config"].task_timeout_s, generate_fn=generate_plan)
                print(json.dumps({"planner_provider": gen.provider, "planner_model": gen.model,
                                  "structured_output": True, "latency_ms": gen.latency_ms}))
                progress(phase="VALIDATE", state="RUNNING", done=2, model=gen.model, timeout=None)
                data = agent_planner.parse_plan(gen.text)
                # CLI-owned identity wins over model output (deterministic).
                data["mission_id"] = args.mission or MISSION_DEFAULT
                data["plan_id"] = plan_id
                data["owner_node"] = ctx["owner_node"]
                data["source_command"] = command
                data["revision"] = revision
                data["engine"] = gen.provider
                data["created_at"] = ""
                plan = agent_planner.enforce_intent(agent_planner.validate_plan_dict(data, allowed))
            except (ProviderError, agent_planner.PlanError) as exc:
                print(json.dumps({"warning": f"model plan failed: {exc}",
                                  "fallback": "deterministic"}))
    if plan is None:
        progress(phase="FALLBACK", state="RUNNING", model="deterministic", timeout=None)
        engine = "deterministic-fallback"
        plan = agent_planner.simple_plan(
            command, args.mission or MISSION_DEFAULT, plan_id, ctx["owner_node"])
        plan.revision = revision
        plan.engine = engine
        progress(done=2)
    if args.allowed_path:
        if agent_planner.is_read_only(command):
            raise agent_planner.PlanError("PLAN_READ_ONLY_TOOL_DENIED")
        data = plan.to_dict()
        for task in data["tasks"]:
            task["allowed_paths"] = list(args.allowed_path)
        plan = agent_planner.validate_plan_dict(data, allowed)
        root = Path(workspace).resolve()
        for rel in args.allowed_path:
            target = (root / rel).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise agent_planner.PlanError("PLAN_SCOPE_PATHS_INVALID", rel)
    progress(phase="SAVE_PLAN", state="RUNNING", done=3)
    path = ctx["state_dir"] / "plans" / f"{plan.plan_id}.json"
    path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    progress(phase="PLAN_COMPLETE", state="DONE", done=4,
             receipt="NOT_APPLICABLE", reference=str(path), result="SAVED")
    print(agent_planner.preview(plan))
    print(f"saved: {path}")
    return 0


def cmd_run(args, ctx) -> int:
    progress = ctx.get("progress", lambda **kw: None)
    progress(phase="LOAD_PLAN")
    if args.provider == "copilot-cloud":
        progress(state="BLOCKED", phase="PROVIDER_GATE", reason="AUDITED_PROVIDER_ROUTING_NOT_RECONCILED", result="BLOCKED")
        print(json.dumps({"status": "BLOCKED", "reason": "AUDITED_PROVIDER_ROUTING_NOT_RECONCILED", "provider": args.provider}))
        return 1
    plan_path = Path(args.plan)
    if not plan_path.is_file():
        plan_path = ctx["state_dir"] / "plans" / f"{args.plan}.json"
    registry = agent_tools.build_default_registry()
    plan = agent_planner.validate_plan_dict(
        json.loads(plan_path.read_text(encoding="utf-8")), registry.names())

    def qc_fn(result, qctx):
        full = {"config": ctx["config"], "store": ctx["store"],
                "workspace": ctx["workspace"], "progress": progress, **qctx}
        return agent_qc.run_qc(result, full)

    report = agent_exec.execute_plan(
        plan, ctx["config"], ctx["store"], ctx["receipts_dir"],
        ctx["state_dir"], ctx["workspace"],
        use_worktree=not args.no_worktree, qc_fn=qc_fn,
        ledger_path=ctx["state_dir"] / "spend_ledger.json",
        progress=progress,
        provider_policy={"provider": args.provider or "auto",
                         "external_authorized": args.allow_external,
                         "billing_mode": args.billing_mode,
                         "test_command": args.test_command,
                         "max_provider_dispatches": 1})
    last = ctx["state_dir"] / "agent_last.json"
    last.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    refs = [t.get("receipt") or t.get("reused_receipt") for t in report["tasks"]]
    progress(phase="RUN_COMPLETE", state="DONE" if report["status"] == "DONE" else "BLOCKED",
             reference=",".join(str(r) for r in refs if r) or "-",
             result=report["status"],
             qc="FAILED" if any(t.get("qc_passed") is False for t in report["tasks"]) else
                "PASSED" if all(t.get("qc_passed") or t.get("decision") == "REUSE" for t in report["tasks"]) else "NOT_RUN",
             receipt="AVAILABLE" if all(refs) else "INCOMPLETE")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("---")
    print(json.dumps(agent_monitor.agent_status({
        "mission_id": plan.mission_id, "owner_node": ctx["owner_node"],
        "store": ctx["store"], "receipts_dir": ctx["receipts_dir"],
        "progress": {"done": report["done"] + report["reused"],
                     "total": len(report["tasks"])},
        "active_model": ",".join(sorted({t.get("provider", "") for t in report["tasks"] if t.get("provider")})),
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
        "next_action": next_action, "evidence": evidence,
        "state_dir": ctx["state_dir"]}), ensure_ascii=False, indent=2))
    return 0


def cmd_providers(args, ctx) -> int:
    print(json.dumps(provider_inventory(
        ctx["config"], ctx["store"], ctx["workspace"]), ensure_ascii=False, indent=2))
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
    finally:
        store = ctx["store"]
        with store._locked():
            previous = store.load_checkpoint(".tool-audit")
            if previous is None and (store.checkpoints / ".tool-audit.json").exists():
                raise ValueError("tool approval audit unreadable")
            events = (previous or {}).get("data", {}).get("events", [])
            store.save_checkpoint(".tool-audit", "tool_policy_audit",
                                  {"events": events + tool_ctx["evidence"]})
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
    receipt_path = ctx["receipts_dir"] / f"{task.task_id}.json"
    report = agent_qc.run_qc(task, {"config": ctx["config"], "store": ctx["store"],
                                    "workspace": ctx["workspace"], "repo": repo,
                                    "receipt_path": str(receipt_path),
                                    "executor_id": task.owner_node,
                                    "executor_runner_id": "km-qc-cli"})
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
    plan.add_argument("--allowed-path", action="append", default=[],
                      help="explicitly approve an existing workspace file for each task; repeat for more files")
    plan.set_defaults(func=cmd_plan)
    run = sub.add_parser("run", help="execute a saved plan")
    run.add_argument("plan")
    run.add_argument("--no-worktree", action="store_true")
    run.add_argument("--provider", choices=["auto", "qwen-local", "codex-cli", "copilot-cli",
                                          "copilot-cloud", "deepseek-local",
                                          "deepseek-api", "muse-cli"], default="")
    run.add_argument("--allow-external", action="store_true", help="authorize one existing-subscription dispatch per task")
    run.add_argument("--billing-mode", choices=["existing-subscription"], default="")
    run.add_argument("--test-command", default="")
    run.add_argument("--remote-repo", default="")
    run.add_argument("--remote-head", default="")
    run.add_argument("--remote-base", default="")
    run.add_argument("--remote-task-id", default="", help="reconcile an existing task without submitting a new prompt")
    run.add_argument("--remote-write", action="store_true", help="authorize remote coding on the existing branch")
    run.set_defaults(func=cmd_run)
    status = sub.add_parser("status", help="monitor view")
    status.set_defaults(func=cmd_status)
    providers = sub.add_parser("providers", help="configured provider quota and availability readback")
    providers.set_defaults(func=cmd_providers)
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
        if args.cmd in ("plan", "run"):
            with LiveProgress(args.cmd) as display:
                ctx["progress"] = display.update
                return args.func(args, ctx)
        return args.func(args, ctx)
    except (ProviderError, agent_planner.PlanError, agent_tools.ToolError,
            agent_exec.AgentError, agent_qc.QCError, FileNotFoundError,
            ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
