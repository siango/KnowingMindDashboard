"""Single-project pilot operations, using the existing registry and authority."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from ..config import WorkerConfig
from ..queue_store import QueueStore
from ..receipts import read_receipt
from ..tasks import ID_RE
from . import memory, planner
from .sandbox import policy_decision

SCHEMA = "KMCW_PROJECT_PILOT_V1"
TOOLS = ("pilot_search", "pilot_context", "pilot_submit", "pilot_run",
         "pilot_read", "account_work", "pilot_events")


def _error(reason):
    from .tools import ToolError
    raise ToolError(reason)


def _json(path):
    if path.stat().st_size > 1_000_000:
        _error("PILOT_CONFIG_OR_PLAN_TOO_LARGE")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        _error("PILOT_EXPECTED_OBJECT")
    return data


def _identifier(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        _error("PILOT_ID_INVALID")
    return value


def validate_config(data):
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        _error("PILOT_CONFIG_INVALID")
    _identifier(data.get("account_id"))
    _identifier(data.get("worker_id"))
    for key in ("workspace", "runtime_base"):
        if not isinstance(data.get(key), str) or not Path(data[key]).is_absolute():
            _error("PILOT_ABSOLUTE_PATH_REQUIRED")
    root = Path(data["workspace"]).resolve()
    if not root.is_dir() or not memory.current_base_commit(str(root)):
        _error("PILOT_GIT_WORKSPACE_REQUIRED")
    for key in ("allowed_paths", "context_paths"):
        paths = data.get(key)
        if (not isinstance(paths, list) or not paths or len(paths) > 10
                or any(not isinstance(p, str) for p in paths)
                or len(paths) != len(set(paths))):
            _error("PILOT_SCOPE_INVALID")
        for rel in paths:
            path = root / rel
            approval = policy_decision("inspect", "pilot", str(root), paths=[rel])
            if (approval["decision"] != "AUTO_APPROVE"
                    or not path.is_file() or path.is_symlink()
                    or any(root.joinpath(*Path(rel).parts[:i]).is_symlink()
                           for i in range(1, len(Path(rel).parts) + 1))):
                _error("PILOT_SCOPE_INVALID")
    approval = policy_decision("test", "pilot", str(root),
                               command=data.get("test_command", ""))
    if approval["decision"] != "AUTO_APPROVE":
        _error("PILOT_TEST_COMMAND_DENIED")
    if data.get("provider") not in {"qwen-local", "copilot-cli"}:
        _error("PILOT_PROVIDER_DENIED")
    if data["provider"] == "copilot-cli" and data.get("authorize_subscription") is not True:
        _error("PILOT_SUBSCRIPTION_AUTHORIZATION_REQUIRED")
    if data.get("memory_ranking", "baseline") not in {"baseline", "qc"}:
        _error("PILOT_RANKING_INVALID")
    return data


def load_context(path):
    data = validate_config(_json(Path(path)))
    if "source_files" in data:
        source = Path(data["source_root"]).resolve()
        for rel, expected in data["source_files"].items():
            path = (source / rel).resolve()
            if (not path.is_relative_to(source) or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                _error("PILOT_INSTALLED_SOURCE_CHANGED")
    base = Path(data["runtime_base"]).resolve()
    state = base / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "plans").mkdir(exist_ok=True)
    # Configure the original adapters, never create a signing key or proof.
    for field, env in (
        ("auditor_root", "KMW2_MISSION_AUDITOR_ROOT"),
        ("authority_snapshot", "KMW2_MISSION_AUDITOR_SNAPSHOT"),
        ("verifier_public_key", "KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY"),
    ):
        value = data.get(field, "")
        if value:
            os.environ[env] = value
        else:
            os.environ.pop(env, None)
    os.environ["KMW2_STATE_DIR"] = str(state)
    config = WorkerConfig.from_env()
    if config.owner_node != data["worker_id"]:
        _error("PILOT_WORKER_ID_MISMATCH")
    return {"pilot": data, "workspace": str(Path(data["workspace"]).resolve()),
            "store": QueueStore(base / "queue"), "receipts_dir": base / "receipts",
            "state_dir": state, "config": config, "evidence": [],
            "account_id": data["account_id"], "mission_id": "G0-G1-MINIMUM-SELLABLE-DEMO"}


def _config(ctx):
    if "pilot" not in ctx:
        _error("PILOT_NOT_CONFIGURED")
    return ctx["pilot"]


def _owned(data, ctx):
    hints = data.get("input", {}).get("hints", {})
    if (hints.get("account_id") != ctx["account_id"]
            or memory.canonical_repo(data.get("input", {})) != ctx["workspace"]):
        _error("PILOT_ACCOUNT_OR_PROJECT_DENIED")


def _plan(plan_id, ctx):
    _identifier(plan_id)
    raw = _json(ctx["state_dir"] / "plans" / f"{plan_id}.json")
    binding = raw.get("pilot_binding", {})
    if (binding.get("account_id") != ctx["account_id"]
            or binding.get("workspace") != ctx["workspace"]):
        _error("PILOT_ACCOUNT_OR_PROJECT_DENIED")
    plan = planner.validate_plan_dict(raw)
    config = _config(ctx)
    if (len(plan.tasks) != 1
            or not set(plan.tasks[0].allowed_paths).issubset(config["allowed_paths"])
            or binding.get("test_command") != config["test_command"]):
        _error("PILOT_SCOPE_CHANGED")
    return plan, binding


def search(args, ctx):
    config = _config(ctx)
    started = time.monotonic()
    goal = args["goal"]
    if not goal.strip() or len(goal) > 4000:
        _error("PILOT_GOAL_INVALID")
    ranking = args.get("ranking", config.get("memory_ranking", "baseline"))
    cases = memory.retrieve_work(
        goal, ctx["workspace"], memory.current_base_commit(ctx["workspace"]),
        ctx["store"], ctx["receipts_dir"], account_id=ctx["account_id"], ranking=ranking)
    return {"cases": cases, "ranking": ranking,
            "search_ms": round((time.monotonic() - started) * 1000, 3),
            "backend": "ORIGINAL_RECEIPTS", "pgvector": "NOT_CONNECTED"}


def context(args, ctx):
    config = _config(ctx)
    paths = args.get("paths", config["context_paths"])
    if (not paths or any(not isinstance(p, str) for p in paths)
            or not set(paths).issubset(set(config["context_paths"]) | set(config["allowed_paths"]))):
        _error("PILOT_CONTEXT_SCOPE_DENIED")
    files, size = {}, 0
    validate_config(config)
    for rel in paths:
        raw = (Path(ctx["workspace"]) / rel).read_bytes()
        size += len(raw)
        if size > 200_000:
            _error("PILOT_CONTEXT_TOO_LARGE")
        files[rel] = raw.decode("utf-8")
    return {"source_sha": memory.current_base_commit(ctx["workspace"]), "files": files,
            "allowed_paths": config["allowed_paths"], "test_command": config["test_command"]}


def submit(args, ctx):
    config = _config(ctx)
    request_id = _identifier(args["request_id"])
    goal = args["goal"]
    paths = args["allowed_paths"]
    acceptance = args["acceptance"]
    if (not isinstance(goal, str) or not goal.strip() or len(goal) > 4000
            or planner.is_read_only(goal)):
        _error("PILOT_CODING_GOAL_REQUIRED")
    if (not paths or any(not isinstance(p, str) for p in paths)
            or not set(paths).issubset(config["allowed_paths"])):
        _error("PILOT_EDIT_SCOPE_DENIED")
    predecessor = args.get("after_task_id", "")
    if predecessor:
        _identifier(predecessor)
    digest = hashlib.sha256(f"{ctx['account_id']}\0{request_id}".encode()).hexdigest()[:24]
    plan_id = f"pilot-{digest}"
    plan = planner.Plan(ctx["mission_id"], plan_id, config["worker_id"], goal,
                        tasks=[planner.PlannedTask(
                            "t1", goal, complexity="S", acceptance=acceptance,
                            allowed_paths=paths, max_repairs=1)],
                        engine="copilot-custom-agent")
    planner.validate_plan_dict(plan.to_dict())
    binding = {"account_id": ctx["account_id"], "workspace": ctx["workspace"],
               "request_id": request_id, "after_task_id": predecessor,
               "test_command": config["test_command"]}
    payload = {**plan.to_dict(), "pilot_binding": binding}
    path = ctx["state_dir"] / "plans" / f"{plan_id}.json"
    with ctx["store"]._locked():
        if path.exists():
            previous = _json(path)
            payload["created_at"] = previous.get("created_at")
            if payload != previous:
                _error("PILOT_IDEMPOTENCY_CONFLICT")
            created = False
        else:
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            temp.replace(path)
            created = True
    saved = _json(path)
    plan_digest = hashlib.sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest()
    ctx["store"].append_event(
        f"{plan_id}-t1", "plan.saved", "SAVED", hashlib.sha256(f"plan|{plan_digest}".encode()).hexdigest(),
        account_id=ctx["account_id"], mission_id=ctx["mission_id"], command_id=plan_id,
        parent_command_id=predecessor or None, prompt_version=f"{planner.SCHEMA}:r1:copilot-custom-agent",
        owner_node=config["worker_id"], source_sha=memory.current_base_commit(ctx["workspace"]),
        detail={"request_id": request_id, "plan_sha256": plan_digest, "allowed_paths": paths},
        provenance="pilot_submit")
    return {"plan_id": plan_id, "task_id": f"{plan_id}-t1", "created": created,
            "status": "PLAN_SAVED_NOT_EXECUTED", "after_task_id": predecessor}


def run(args, ctx):
    from .executor import execute_plan
    config = _config(ctx)
    plan, binding = _plan(args["plan_id"], ctx)
    predecessor = binding["after_task_id"]
    if predecessor:
        path = ctx["receipts_dir"] / f"{predecessor}.json"
        if not path.is_file():
            return _record_report(plan.plan_id, {
                "status": "BLOCKED", "reason": "PREDECESSOR_RECEIPT_MISSING",
                "task_id": predecessor}, ctx)
        receipt = read_receipt(path)
        _owned(receipt, ctx)
        if not memory.receipt_verified(receipt) or memory.terminal_conflicts(ctx["store"], receipt):
            return _record_report(plan.plan_id, {
                "status": "BLOCKED", "reason": "PREDECESSOR_NOT_VERIFIED",
                "task_id": predecessor}, ctx)
    _node_evidence(plan.plan_id, config, ctx)
    started = time.monotonic()
    report = execute_plan(
        plan, ctx["config"], ctx["store"], ctx["receipts_dir"], ctx["state_dir"],
        ctx["workspace"], provider_policy={
            "provider": config["provider"], "account_id": ctx["account_id"],
            "worker_id": config["worker_id"],
            "external_authorized": config.get("authorize_subscription", False),
            "billing_mode": "existing-subscription" if config["provider"] == "copilot-cli" else "",
            "max_provider_dispatches": 1, "test_command": config["test_command"],
            "memory_ranking": config.get("memory_ranking", "baseline")})
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    return _record_report(plan.plan_id, report, ctx)


def _node_evidence(plan_id, config, ctx):
    """Observed local node/tool/skill identity for this run; no remote health is implied."""
    import socket
    from .tools import build_default_registry
    contract = build_default_registry().command_contract()
    agent = ctx["workspace"] and Path(ctx["workspace"]) / ".github" / "agents" / "kmcw.agent.md"
    skill_sha = hashlib.sha256(agent.read_bytes()).hexdigest() if agent and agent.is_file() else None
    evidence = {"node_id": config["worker_id"], "hostname": socket.gethostname()[:80],
                "node_state": "LIVE_LOCAL_PROCESS", "tool_contract_sha256": contract["sha256"],
                "tool_count": contract["command_count"], "skill": "kmcw custom agent",
                "skill_sha256": skill_sha, "provider_route": config["provider"],
                "remote_nodes": "NOT_OBSERVED_BY_THIS_RUN"}
    ctx["store"].append_event(
        f"{plan_id}-t1", "node.evidence", "OBSERVED",
        hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
        account_id=ctx["account_id"], mission_id=ctx["mission_id"], command_id=plan_id,
        owner_node=config["worker_id"], tool="ToolRegistry", skill="kmcw.agent.md",
        detail=evidence, provenance="pilot_run local observation")


def _record_report(plan_id, report, ctx):
    # Derived readback only: QueueStore/receipts remain the execution authority.
    with ctx["store"]._locked():
        ctx["store"].save_checkpoint(f".pilot-{plan_id}", "pilot_run_readback",
                                    {"account_id": ctx["account_id"], "report": report})
    return report


# Token counts are provider-reported only from these usage sources. Legacy
# receipts without usage_source fall back to the provider name.
MEASURED_USAGE_SOURCES = {"OLLAMA_EVAL_COUNTS", "OPENAI_COMPATIBLE_USAGE_FIELDS",
                          "COPILOT_CLI_USAGE_OUTPUT_FILE"}
LEGACY_MEASURED_PROVIDERS = {"qwen-local", "deepseek-local", "deepseek-api"}


def _measured(event):
    source = event.get("usage_source")
    if source is not None:
        return source in MEASURED_USAGE_SOURCES
    return event.get("provider") in LEGACY_MEASURED_PROVIDERS


def _dedup(events):
    seen, kept, duplicates = set(), [], 0
    for event in events:
        key = event.get("idempotency_key")
        if key and key in seen:
            duplicates += 1
            continue
        if key:
            seen.add(key)
        kept.append(event)
    return kept, duplicates


def usage(receipt):
    evidence = [e for e in receipt.get("evidence", []) if isinstance(e, dict)]
    events, duplicates = _dedup([e for e in evidence if e.get("event") == "model_generated"])
    failed, failed_duplicates = _dedup([e for e in evidence if e.get("event") == "provider_paused"
                                        and isinstance(e.get("usage"), dict)])
    def total(field, rows):
        values = [e.get(field) for e in rows]
        if not values:
            return None
        return sum(values) if all(type(v) in (int, float) for v in values) else None
    def native(field, rows):
        values = [(e.get("native_usage") or e.get("usage") or {}).get(field) for e in rows]
        values = [v for v in values if v is not None]
        return sum(values) if values and all(type(v) is int for v in values) else None
    measured = [e for e in events if _measured(e)]
    unmeasured = sorted({str(e.get("usage_source") or e.get("provider")) for e in events
                         if not _measured(e)})
    complete = len(measured) == len(events)
    failed_measured = [e["usage"] for e in failed
                       if e["usage"].get("usage_source") in MEASURED_USAGE_SOURCES]
    return {"recorded_generations": len(events),
            "duplicate_generation_events_ignored": duplicates + failed_duplicates,
            "external_generations": sum(e.get("provider") in {"copilot-cli", "codex-cli", "deepseek-api"}
                                        for e in events),
            "tokens_in": total("tokens_in", measured) if complete else None,
            "tokens_out": total("tokens_out", measured) if complete else None,
            "token_basis": ("PROVIDER_REPORTED" if complete and events else
                            "NONE_RECORDED" if not events else "UNKNOWN_FOR_" + ",".join(unmeasured)),
            "tokens_measured_partial": {"generations": len(measured),
                                        "tokens_in": total("tokens_in", measured),
                                        "tokens_out": total("tokens_out", measured)},
            "buckets": {
                "actual": {"tokens_in": total("tokens_in", measured),
                           "tokens_out": total("tokens_out", measured),
                           "copilot_premium_requests": native("premium_requests", measured),
                           "copilot_nano_aiu": native("nano_aiu", measured),
                           "failed_dispatch_tokens_in": total("tokens_in", failed_measured),
                           "failed_dispatch_tokens_out": total("tokens_out", failed_measured),
                           "billed_cost_usd": None},
                "estimated": {"cost_usd": total("estimated_cost_usd",
                                                [e for e in events if e.get("estimated_cost_usd") is not None]),
                              "basis": "measured tokens x configured price; not billed"},
                "unknown": unmeasured + (["failed_dispatch_usage"] if len(failed_measured) < len(failed) else []),
                "reservations": "BUDGET_HOLDS_EXCLUDED_NEVER_BILLED"},
            "usage_scope": "CUSTOMER_TASK",
            "generation_ms": total("latency_ms", events), "known_actual_cost_usd": total("actual_cost_usd", events),
            "estimated_cost_usd": total("estimated_cost_usd", events),
            "repair_rounds": sum(e.get("event") == "edit_failed" for e in evidence),
            "outcome_bucket": "QC_PASSED" if memory.receipt_verified(receipt) else "NOT_QC_PASSED",
            "completeness": "RECORDED_GENERATIONS_ONLY_FAILED_OR_INTERRUPTED_USAGE_MAY_BE_UNKNOWN"}


def orchestration_usage(path, plan_id):
    """Copilot session usage of the customer's orchestrating agent; never task cost."""
    from .providers_ext import parse_copilot_usage
    _identifier(plan_id)
    parsed = parse_copilot_usage(Path(path))
    raw = Path(path).read_bytes() if Path(path).is_file() else b""
    return {"usage_scope": "ORCHESTRATION_NOT_CUSTOMER_TASK", "plan_id": plan_id,
            "linkage": "OPERATOR_ASSERTED_PLAN_ID_NO_PROVIDER_SESSION_ID",
            "source_sha256": hashlib.sha256(raw).hexdigest() if raw else None, **parsed}


def account_work(args, ctx):
    _config(ctx)
    task_id = _identifier(args["task_id"])
    path = ctx["receipts_dir"] / f"{task_id}.json"
    if path.is_file():
        receipt = read_receipt(path)
        _owned(receipt, ctx)
        conflicts = memory.terminal_conflicts(ctx["store"], receipt)
        return {"account_id": ctx["account_id"], "task_id": task_id,
                "worker_id": receipt["input"]["hints"].get("worker_id", receipt["owner_node"]),
                "status": receipt["status"], "verified": memory.receipt_verified(receipt) and not conflicts,
                "conflicts": conflicts, "receipt": str(path),
                "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "output": receipt["output"], "usage": usage(receipt),
                "payment_status": "PAYMENT_UNVERIFIED", "verified_revenue": None}
    for folder in (ctx["store"].inbox, ctx["store"].doing,
                   ctx["store"].done, ctx["store"].failed):
        candidate = folder / f"{task_id}.json" if folder in (
            ctx["store"].inbox, ctx["store"].doing) else folder / task_id / "task.json"
        if candidate.is_file():
            data = _json(candidate)
            _owned(data, ctx)
            return {"account_id": ctx["account_id"], "task_id": task_id,
                    "worker_id": data["input"]["hints"].get("worker_id", data["owner_node"]),
                    "status": data["status"], "verified": False, "receipt": None,
                    "payment_status": "PAYMENT_UNVERIFIED", "verified_revenue": None}
    _error("PILOT_TASK_NOT_FOUND")


def read(args, ctx):
    plan, _ = _plan(args["plan_id"], ctx)
    task_id = f"{plan.plan_id}-t1"
    checkpoint = ctx["store"].load_checkpoint(f".pilot-{plan.plan_id}")
    path = ctx["receipts_dir"] / f"{task_id}.json"
    result = {"plan_id": plan.plan_id, "task_id": task_id,
              "run": (checkpoint or {}).get("data", {}).get("report"),
              "receipt": None, "payment_status": "PAYMENT_UNVERIFIED"}
    if path.is_file():
        receipt = read_receipt(path)
        _owned(receipt, ctx)
        result.update(receipt=receipt, account_work=account_work({"task_id": task_id}, ctx))
    return result


def events(args, ctx):
    """Account-scoped readback of the existing append-only event spool."""
    _config(ctx)
    task_id = _identifier(args["task_id"])
    data = ctx["store"].read_events(task_id)
    if data["event_count"] == 0:
        _error("PILOT_TASK_NOT_FOUND")
    if any(event.get("account_id") != ctx["account_id"] for event in data["events"]):
        _error("PILOT_ACCOUNT_OR_PROJECT_DENIED")
    accounting = {"provider_events": 0, "tokens_in": None, "tokens_out": None,
                  "unknown_usage_events": 0, "billed_cost_usd": None}
    measured_in, measured_out = [], []
    for event in data["events"]:
        if not event["step"].startswith("provider."):
            continue
        accounting["provider_events"] += 1
        usage_row = event.get("usage") or {}
        if event.get("usage_source") in MEASURED_USAGE_SOURCES and \
                type(usage_row.get("tokens_in")) is int and type(usage_row.get("tokens_out")) is int:
            measured_in.append(usage_row["tokens_in"])
            measured_out.append(usage_row["tokens_out"])
        else:
            accounting["unknown_usage_events"] += 1
    if measured_in:
        accounting.update(tokens_in=sum(measured_in), tokens_out=sum(measured_out))
    return {**{k: v for k, v in data.items() if k != "events"},
            "events": data["events"][-int(args.get("limit", 50)):],
            "accounting": accounting, "payment_status": "PAYMENT_UNVERIFIED"}


def register_pilot_tools(registry):
    from .tools import ToolSpec
    specs = (
        ("pilot_search", search, {"goal": "str"}, {"ranking": "str"}, "read"),
        ("pilot_context", context, {}, {"paths": "list"}, "read"),
        ("pilot_submit", submit, {"request_id": "str", "goal": "str", "allowed_paths": "list",
                                 "acceptance": "list"}, {"after_task_id": "str"}, "write"),
        ("pilot_run", run, {"plan_id": "str"}, {}, "write"),
        ("pilot_read", read, {"plan_id": "str"}, {}, "read"),
        ("account_work", account_work, {"task_id": "str"}, {}, "read"),
        ("pilot_events", events, {"task_id": "str"}, {"limit": "int"}, "read"),
    )
    for name, fn, required, optional, permission in specs:
        registry.register(ToolSpec(
            name, "single-project pilot: " + name, required, optional, permission,
            "existing plans/queue/worktree/receipts only" if permission == "write" else "none",
            "installed account/project scope; original audit and QC gates"),
            fn, lambda ctx: (True, "OK") if "pilot" in ctx else (False, "PILOT_NOT_CONFIGURED"))
