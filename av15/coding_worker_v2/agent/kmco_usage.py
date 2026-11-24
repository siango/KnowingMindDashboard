"""KMCO automatic PostgreSQL audit, using the existing km_microops database.

The DB connection is opt-in and owner-configured via a libpq service file.
No database/role/extension is provisioned, no SQL is selected by a caller,
no tool arguments or secrets are stored, and no vector ranking grants rights.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

SERVICE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
OP_RE = re.compile(r"^(?:P|D)\d{2}-M\d{2}$")


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _operation_ids(name, args):
    group = {"kmco.p01.evidence": "P01",
             "kmco.p02.resume": "P02",
             "kmco.p03.loop_guard": "P03",
             "kmco.p04.plan": "P04",
             "kmco.p05.completion": "P05",
             "kmco.p06.permission": "P06",
             "kmco.p07.checkpoint": "P07",
             "kmco.p08.memory": "P08"}.get(name)
    domain = {"kmco.d01.authority": "D01", "kmco.d02.read": "D02",
              "kmco.d12.backup_restore": "D12"}.get(name)
    if domain:
        return [f"{domain}-M{i:02d}" for i in range(1, 7)]
    if group:
        return [f"{group}-M{i:02d}" for i in range(1, 6)]
    if isinstance(args, dict):
        if isinstance(args.get("operation_id"), str):
            return [args["operation_id"]] if OP_RE.fullmatch(args["operation_id"]) else []
        steps = args.get("steps")
        if isinstance(steps, list):
            return sorted(set(s["op_id"] for s in steps
                              if isinstance(s, dict)
                              and isinstance(s.get("op_id"), str)
                              and OP_RE.fullmatch(s["op_id"])))[:32]
    return []


def record(name, args, ctx, *, ok, result_sha256=None, error_class=None, return_ref=False, idempotent=False):
    """Persist a bounded usage observation. Never promotes QC or receipt status.

    If service is absent, returns NOT_CONFIGURED (no pretend persistence).
    When configured, a failure raises and caller must disclose audit failure.
    """
    if not name.startswith("kmco."):
        return "NOT_KMCO"
    service = ctx.get("kmco_pg_service", os.environ.get("KMCO_PG_SERVICE", ""))
    if not service:
        return "NOT_CONFIGURED"
    if not SERVICE_RE.fullmatch(service):
        raise RuntimeError("KMCO_PG_SERVICE_INVALID")
    if result_sha256 is not None and not DIGEST_RE.fullmatch(result_sha256):
        raise RuntimeError("KMCO_RESULT_DIGEST_INVALID")
    if not isinstance(args, dict):
        args = {}
    try:
        import psycopg
    except ImportError:
        raise RuntimeError("KMCO_PG_DRIVER_MISSING") from None
    # Connect through libpq service credentials provisioned by the operator.
    # Never accept DSN/SQL/role/target through a tool argument.
    params = {
        "operation": name, "operation_ids": _operation_ids(name, args),
        "mission_id": str(ctx.get("mission_id") or "")[:128],
        "task_id": str(ctx.get("task_id") or "")[:128],
        "source_sha": str(ctx.get("source_sha") or "")[:64],
        "args_sha256": _digest(args),
        "result_sha256": result_sha256,
        "success": bool(ok),
        "error_class": str(error_class)[:100] if error_class else None,
    }
    try:
        with psycopg.connect("service=" + service, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database()")
                if cur.fetchone() != ("km_microops",):
                    raise RuntimeError("KMCO_DB_TARGET_MISMATCH")
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                statement = """
                    INSERT INTO circuit.usage_events
                      (operation, operation_ids, mission_id, task_id, source_sha,
                       args_sha256, result_sha256, success, error_class)
                    VALUES (%(operation)s, %(operation_ids)s, %(mission_id)s,
                            %(task_id)s, %(source_sha)s, %(args_sha256)s,
                            %(result_sha256)s, %(success)s, %(error_class)s)
                """
                if idempotent:
                    # KARL replay/resume: retain the exact original invocation, never
                    # inflate usage_events or core_memory.attempt_count on delivery.
                    lock_key = _digest([params["mission_id"], params["task_id"],
                                        name, params["args_sha256"]])
                    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                                (lock_key,))
                    cur.execute("""
                        SELECT id FROM circuit.usage_events
                        WHERE mission_id=%s AND task_id=%s
                          AND operation=%s AND args_sha256=%s
                        ORDER BY id ASC LIMIT 1
                    """, (params["mission_id"], params["task_id"], name,
                          params["args_sha256"]))
                    existing = cur.fetchone()
                    if existing:
                        if return_ref:
                            return f"postgres:km_microops:circuit.usage_events:{existing[0]}"
                        return "ALREADY_PERSISTED_UNVERIFIED"
                cur.execute(statement + (" RETURNING id" if return_ref else ""), params)
                event_id = cur.fetchone()[0] if return_ref else None
                # Same DB transaction as the usage event. Never save a derived
                # four-part case if its original execution event was not saved.
                from .kmco_core import observe
                observe(cur, name, params["args_sha256"],
                        params["operation_ids"], ctx, ok=ok,
                        result_sha256=result_sha256, error_class=error_class)
    except Exception:
        # Never print connection URL, driver exception or SQL credentials.
        raise RuntimeError("KMCO_DB_AUDIT_WRITE_FAILED") from None
    if return_ref:
        if type(event_id) is not int or event_id < 1:
            raise RuntimeError("KMCO_AUDIT_REFERENCE_INVALID")
        return f"postgres:km_microops:circuit.usage_events:{event_id}"
    return "PERSISTED_UNVERIFIED"



def record_karl_event(store, task_id, event_id):
    """Replay one intact, authorized KMCW ledger event through the existing writer.

    No new DSN/key/database or direct table mutation. The DB service is the
    same owner-configured libpq service as existing KMCO usage. If absent,
    the source ledger is left available for later reconciliation.
    """
    snapshot = store.read_events(task_id)
    if not snapshot["chain_intact"]:
        raise RuntimeError("KARL_EVENT_CHAIN_INVALID")
    matching = [event for event in snapshot["events"]
                if event.get("event_id") == event_id
                and event.get("step") == "karl.sandbox.observed"]
    if len(matching) != 1:
        raise RuntimeError("KARL_EVENT_IDENTITY_INVALID")
    event = matching[0]
    observation = event.get("detail")
    if (not isinstance(observation, dict)
            or observation.get("schema") != "KMCW_SANDBOX_OBSERVATION_V1"
            or event.get("task_id") != task_id
            or observation.get("task_id") != task_id
            or observation.get("mission_id") != event.get("mission_id")
            or observation.get("source_sha") != event.get("source_sha")
            or observation.get("status") != event.get("status")):
        raise RuntimeError("KARL_OBSERVATION_SCOPE_MISMATCH")
    result_hash = observation.get("output_sha256")
    if not isinstance(result_hash, str) or not DIGEST_RE.fullmatch(result_hash):
        raise RuntimeError("KARL_OUTPUT_DIGEST_INVALID")
    context = {"mission_id": event["mission_id"], "task_id": task_id,
               "source_sha": event["source_sha"]}
    args = {"queue_event_id": event_id}
    persistence = record("kmco.karl.sandbox", args, context,
                         ok=event["status"] == "PASS",
                         result_sha256=result_hash,
                         error_class=observation.get("failure_class"),
                         idempotent=True)
    if persistence == "NOT_CONFIGURED":
        return "CORE_WRITER_NOT_CONFIGURED_LOCAL_EVENT_DURABLE"
    if persistence not in ("PERSISTED_UNVERIFIED",
                           "ALREADY_PERSISTED_UNVERIFIED"):
        raise RuntimeError("KARL_CORE_WRITER_STATUS_UNKNOWN")
    # Separate read-only database connection. Never treat local spool or a
    # successful INSERT as proof of a committed database readback.
    from .kmco_core import readback_exact
    readback = readback_exact(context, "kmco.karl.sandbox",
                              _digest(args), result_hash)
    return ("ALREADY_" if persistence == "ALREADY_PERSISTED_UNVERIFIED"
            else "") + readback
