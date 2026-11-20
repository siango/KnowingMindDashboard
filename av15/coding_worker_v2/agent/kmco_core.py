"""KMCO Know-Remember-Do derived work memory on existing km_microops.

Four parts map strictly to KEBUNATE engineering language. This is not
Buddhist Canon, verified cause, an authority grant or independent QC.
No new service or data store is started.
"""
from __future__ import annotations

import json
import os
import re

SERVICE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


def _service():
    service = os.environ.get("KMCO_PG_SERVICE", "")
    if not service:
        raise RuntimeError("KMCO_CORE_PG_SERVICE_NOT_CONFIGURED")
    if not SERVICE_RE.fullmatch(service):
        raise RuntimeError("KMCO_CORE_SERVICE_INVALID")
    return service


def _scope(ctx):
    mission = ctx.get("mission_id")
    task = ctx.get("task_id")
    if (not isinstance(mission, str) or not 1 <= len(mission) <= 128
            or not isinstance(task, str) or not 1 <= len(task) <= 128):
        raise RuntimeError("KMCO_CORE_SCOPE_REQUIRED")
    return mission, task


def observe(cur, name, args_hash, operation_ids, ctx, *, ok,
            result_sha256=None, error_class=None):
    """Transactional projection after an actual KMCW ToolRegistry call.

    Called from existing usage logger's transaction, never directly as
    an independently authorized DB writer.
    """
    mission, task = _scope(ctx)
    source_sha = str(ctx.get("source_sha") or "")[:64]
    status = "EXECUTED_UNVERIFIED" if ok else "BLOCKED_UNVERIFIED"
    # Preserve the distinction between observed failure and hypothesized cause.
    dukkha = {"observed": "NONE_REPORTED" if ok else (error_class or "TOOL_FAILED"),
              "evidence": "KMCW.ToolRegistry.tool_called"}
    samudaya = {"cause": None, "verification": "NOT_DIAGNOSED"}
    nirodha = {"acceptance_criterion": "AUTHORIZED_TOOL_CALL_AND_INDEPENDENT_QC",
               "independent_qc": "NOT_RUN", "verified": False}
    magga = {"selected_handler": name, "executed": bool(ok),
             "result_sha256": result_sha256}
    payload = {
        "mission": mission, "task": task, "name": name, "ops": operation_ids,
        "args_hash": args_hash, "source_sha": source_sha,
        "dukkha": json.dumps(dukkha, ensure_ascii=False),
        "samudaya": json.dumps(samudaya, ensure_ascii=False),
        "nirodha": json.dumps(nirodha, ensure_ascii=False),
        "magga": json.dumps(magga, ensure_ascii=False),
        "state": status, "result_hash": result_sha256,
    }
    cur.execute("""
      INSERT INTO circuit.core_memory
       (mission_id,task_id,operation,operation_ids,args_sha256,source_sha,
        dukkha,samudaya,nirodha,magga,result_state,result_sha256)
      VALUES
       (%(mission)s,%(task)s,%(name)s,%(ops)s,%(args_hash)s,%(source_sha)s,
        %(dukkha)s::jsonb,%(samudaya)s::jsonb,%(nirodha)s::jsonb,
        %(magga)s::jsonb,%(state)s,%(result_hash)s)
      ON CONFLICT (mission_id,task_id,operation,args_sha256)
      DO UPDATE SET operation_ids=EXCLUDED.operation_ids,
        source_sha=EXCLUDED.source_sha,dukkha=EXCLUDED.dukkha,
        samudaya=EXCLUDED.samudaya,nirodha=EXCLUDED.nirodha,
        magga=EXCLUDED.magga,result_state=EXCLUDED.result_state,
        result_sha256=EXCLUDED.result_sha256,
        attempt_count=circuit.core_memory.attempt_count+1,
        updated_at=now()
    """, payload)


def recall(ctx, *, limit=10):
    """Retrieve same-mission and same-task derived records, not other owners."""
    mission, task = _scope(ctx)
    service = _service()
    if type(limit) is not int or not 1 <= limit <= 20:
        raise RuntimeError("KMCO_CORE_LIMIT_INVALID")
    try:
        import psycopg
    except ImportError:
        raise RuntimeError("KMCO_CORE_PG_DRIVER_MISSING") from None
    try:
        with psycopg.connect("service=" + service, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT current_database()")
                if cur.fetchone() != ("km_microops",):
                    raise RuntimeError("KMCO_CORE_DB_TARGET_MISMATCH")
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                cur.execute("""
                    SELECT operation,operation_ids,source_sha,dukkha,
                           samudaya,nirodha,magga,result_state,result_sha256,
                           attempt_count,updated_at
                    FROM circuit.core_memory
                    WHERE mission_id=%s AND task_id=%s
                    ORDER BY updated_at DESC LIMIT %s
                """, (mission, task, limit))
                rows = cur.fetchall()
                return {
                    "schema": "KMCO_CORE_MEMORY_READ_V1",
                    "state": "DERIVED_UNVERIFIED",
                    "mission_id": mission, "task_id": task,
                    "cases": [
                        {"operation": r[0], "operation_ids": r[1],
                         "source_sha": r[2], "dukkha": r[3],
                         "samudaya": r[4], "nirodha": r[5], "magga": r[6],
                         "result_state": r[7], "result_sha256": r[8],
                         "attempt_count": r[9], "updated_at": r[10].isoformat()}
                        for r in rows
                    ],
                    "independent_qc": "NOT_RUN",
                }
    except Exception:
        raise RuntimeError("KMCO_CORE_RECALL_UNAVAILABLE") from None
