"""Thai/English intake inside the existing pilot; no chatbot service or authority.

The transport supplies authenticated owner command metadata separately from tool
arguments. Natural language can request an operation; it cannot authorize it.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import memory, planner
from .executor import completion_readback, plan_readiness

INTENTS = {"ADVISE", "ARCHITECT", "PLAN", "MISSION", "STATUS", "DIAGNOSE",
           "EXECUTE", "PAUSE", "RESUME", "APPROVE"}


def route_intent(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("CONVERSATION_TEXT_INVALID")
    value = text.strip()
    explicit = re.match(r"^(ADVISE|ARCHITECT|PLAN|MISSION|STATUS|DIAGNOSE|EXECUTE|PAUSE|RESUME|APPROVE)(?:\b|:)",
                        value, re.I)
    # Questions/conditionals are consultation even when they mention execution.
    if re.search(r"\b(should|could|would|what if|how to|how do|can you|if we)\b|ควร|ถ้า|ดีไหม|อย่างไร|ได้ไหม|หรือไม่", value, re.I):
        return "PLAN" if re.search(r"\bplan\b|วางแผน|แผนงาน", value, re.I) else "ADVISE"
    if explicit:
        return explicit.group(1).upper()
    routes = (("PLAN", r"^(?:ช่วย)?(?:วางแผน|ปรับแผน|แก้แผน)|^(?:please )?(?:plan|revise the plan)\b"),
              ("ARCHITECT", r"ออกแบบสถาปัตยกรรม|ตรวจสถาปัตยกรรม|\barchitecture\b"),
              ("STATUS", r"สถานะ|ความคืบหน้า|\b(?:status|progress)\b"),
              ("DIAGNOSE", r"วิเคราะห์สาเหตุ|หาสาเหตุ|ติดอะไร|\bdiagnose\b"),
              ("PAUSE", r"^(?:หยุดชั่วคราว|พักงาน|pause)"),
              ("RESUME", r"^(?:ทำงานต่อ|ต่องาน|ดำเนินงานต่อ|resume)"),
              ("EXECUTE", r"^(?:ลงมือ|ดำเนินการ|รันงาน|execute|run the task)"),
              ("APPROVE", r"^(?:อนุมัติ|approve)"),
              ("MISSION", r"ค้นหาภารกิจ|สร้างภารกิจ|\bmission\b"))
    return next((intent for intent, pattern in routes if re.search(pattern, value, re.I)), "ADVISE")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _owner_command(ctx, plan, request, intent, source):
    """Validate claims injected by existing authenticated Admin middleware.

    This is not authentication, a signing mechanism, or approval issuance.
    Client arguments have no principal/approval field. Missing integration fails
    closed. Execution's existing ADMIT/auditor gates still apply after this check.
    """
    claims = ctx.get("owner_command", {})
    expected = {"account_id": ctx["account_id"], "mission_id": plan.mission_id,
                "plan_id": plan.plan_id, "source_sha": source,
                "request_sha256": _digest(request)}
    return (isinstance(claims, dict) and claims.get("authenticated") is True
            and isinstance(claims.get("subject"), str) and bool(claims["subject"].strip())
            and all(claims.get(k) == v for k, v in expected.items())
            and claims.get("permissions") == ["mission:" + intent.lower()])


def discuss(args, ctx):
    from . import pilot
    from .tools import build_default_registry
    import fcntl
    plan, _ = pilot._plan(args["plan_id"], ctx)
    if plan.mission_id != ctx["mission_id"]:
        pilot._error("CONVERSATION_MISSION_IDENTITY_MISMATCH")
    session_id = pilot._identifier(args["session_id"])
    request_id = pilot._identifier(args["request_id"])
    text = args["text"]
    intent = route_intent(text)
    key = f".conversation-{session_id}"
    store = ctx["store"]
    with (store.checkpoints / f"{key}.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        checkpoint = store.load_checkpoint(key)
        if checkpoint is None and (store.checkpoints / f"{key}.json").exists():
            pilot._error("CONVERSATION_CHECKPOINT_UNREADABLE")
        identity = {"account_id": ctx["account_id"], "mission_id": plan.mission_id,
                    "plan_id": plan.plan_id, "workspace": ctx["workspace"]}
        saved = (checkpoint or {}).get("data", {})
        if saved and saved.get("identity") != identity:
            pilot._error("CONVERSATION_IDENTITY_CONFLICT")
        requests = saved.get("requests", {})
        def persist_session():
            payload = {"identity": identity, "requests": requests, "last_request_id": request_id}
            if len(json.dumps(payload, ensure_ascii=False).encode()) > 1_000_000:
                pilot._error("CONVERSATION_SESSION_BOUND_REACHED")
            with store._locked():
                store.save_checkpoint(key, "conversation", payload)
        request = {"session_id": session_id, "request_id": request_id,
                   "plan_id": plan.plan_id, "text": text}
        if request_id in requests:
            previous = requests[request_id]
            if previous["request_sha256"] != _digest(request):
                pilot._error("CONVERSATION_REQUEST_IDEMPOTENCY_CONFLICT")
            return previous["response"]
        if len(requests) >= 16:
            pilot._error("CONVERSATION_SESSION_BOUND_REACHED")
        # Read both local memory and existing project/source/SSOT projections
        # before answering. No model inference is required for intent/authority.
        context = pilot.context({}, ctx)
        source = context["source_sha"]
        readiness = plan_readiness(plan, store, ctx["receipts_dir"], ctx["workspace"],
                                   account_id=ctx["account_id"])
        lessons = memory.planning_lessons(plan.source_command, ctx["workspace"], source, store,
            ctx["receipts_dir"], account_id=ctx["account_id"], plan=plan,
            context=ctx)
        proofs = {t.task_id: completion_readback(plan, t.task_id, store,
            ctx["receipts_dir"], ctx["workspace"], account_id=ctx["account_id"]) for t in plan.tasks}
        controller = store.load_checkpoint(f".reconcile-{plan.plan_id}") or {}
        registry = ctx.get("tool_registry") or build_default_registry()
        contract = registry.command_contract()
        response = {"intent": intent, **identity, "session_id": session_id,
                    "task_ids": [f"{plan.plan_id}-{t.task_id}" for t in plan.tasks],
                    "REALITY": {"source_sha": source, "readiness": readiness,
                        "memory": lessons, "receipts": proofs,
                        "last_controller": controller.get("data", {}).get("report"),
                        "architecture_refs": {p: hashlib.sha256(v.encode()).hexdigest()
                                              for p, v in context["files"].items()},
                        "tool_contract_sha256": contract["sha256"],
                        "central_sync": "UNSYNCED", "kmnw": "PREVIEW_ONLY",
                        "admit": "REQUIRES_EXISTING_AUDITOR_GATE"},
                    "ASSUMPTION": [], "PROPOSAL": [], "execution": "NOT_REQUESTED",
                    "reply": "Read the existing source, memory and task checkpoint; no completion inferred.",
                    "previous_request_id": saved.get("last_request_id")}
        if intent in {"ADVISE", "ARCHITECT", "PLAN", "DIAGNOSE"}:
            # Evidence versus root-cause hypotheses stay structurally separate.
            response["PROPOSAL"] = [{"kind": intent, "status": "PROPOSED_ONLY",
                "same_mission": True, "task_dag": [{"task_id": t.task_id, "deps": t.deps,
                "acceptance": t.acceptance, "paths": t.allowed_paths,
                "source_sha": (t.execution_contract or {}).get("source_sha")}
                for t in planner.order_tasks(plan)],
                'method_selection': planner.select_method(lessons),
                "memory_recommendations": [l["recommendation"] for l in lessons["lessons"]
                                           if l["classification"] == "VERIFIED"
                                           and l.get('recommendation') is not None],
                "current_state": "Existing Planner -> QueueStore -> executor -> independent QC -> receipts",
                "proposed_state": "Same components with bounded reconcile_once and conversational pilot intake",
                "evaluation": {
                    "compatibility": "Original plan/task IDs and full audit scope retained",
                    "security": "Existing ADMIT, ACL, lease, verifier and receipt gates remain mandatory",
                    "cost": "No inferred savings; local/deterministic first, external spend denied here",
                    "feasibility": "Local fixture contracts; live capability and authority qualification pending",
                    "rollback": "Remove only this additive source delta; retain queue, receipts and checkpoints"},
                "next_safe_action": "Reuse this DAG; revise with existing decompose_plan under explicit scope."}]
            response["reply"] = ("ข้อเท็จจริงและหลักฐานอยู่ใน REALITY; แผนเสนออยู่ใน PROPOSAL ยังไม่ได้สั่งรัน"
                                   if re.search(r"[ก-๙]", text) else
                                   "Evidence is in REALITY; the existing DAG and memory recommendations are proposals.")
        elif intent == "MISSION":
            response.update(reply="Existing mission discovered; new mission writes are unavailable in this continuation.",
                            mission_write="DENIED_NO_AUTHORIZED_MISSION_WRITE_CONTRACT", created=False)
        elif intent == "APPROVE":
            response.update(reply="Scoped approval requires the existing verified owner approval contract.",
                            execution="BLOCKED_APPROVAL_CONTRACT_UNAVAILABLE")
        elif intent in {"EXECUTE", "PAUSE", "RESUME"}:
            if not _owner_command(ctx, plan, request, intent, source):
                response["execution"] = "BLOCKED_VERIFIED_SCOPED_OWNER_COMMAND_REQUIRED"
            else:
                # Reserve the request before side effects. Interrupted/replayed
                # commands return a pending readback; never execute them twice.
                response["execution"] = "COMMAND_RESERVED_REQUIRES_READBACK"
                requests[request_id] = {"request_sha256": _digest(request), "response": response}
                persist_session()
                if intent in {"PAUSE", "RESUME"}:
                    with store._locked():
                        store.save_checkpoint(f".control-{plan.plan_id}", "owner_control", {
                            **identity, "paused": intent == "PAUSE",
                            "request_sha256": _digest(request), "subject": ctx["owner_command"]["subject"]})
                    response["execution"] = "PAUSED" if intent == "PAUSE" else "RESUMED_GATES_UNCHANGED"
                else:
                    response["controller"] = pilot.reconcile({"plan_id": plan.plan_id}, ctx)
                    response["execution"] = response["controller"]["status"]
        requests[request_id] = {"request_sha256": _digest(request), "response": response}
        persist_session()
        return response
