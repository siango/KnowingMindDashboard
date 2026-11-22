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
    if name in ("kmco.p01.evidence",):
        return [f"P01-M{i:02d}" for i in range(1, 6)]
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


def record(name, args, ctx, *, ok, result_sha256=None, error_class=None):
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
                cur.execute("""
                    INSERT INTO circuit.usage_events
                      (operation, operation_ids, mission_id, task_id, source_sha,
                       args_sha256, result_sha256, success, error_class)
                    VALUES (%(operation)s, %(operation_ids)s, %(mission_id)s,
                            %(task_id)s, %(source_sha)s, %(args_sha256)s,
                            %(result_sha256)s, %(success)s, %(error_class)s)
                """, params)
                # Same DB transaction as the usage event. Never save a derived
                # four-part case if its original execution event was not saved.
                from .kmco_core import observe
                observe(cur, name, params["args_sha256"],
                        params["operation_ids"], ctx, ok=ok,
                        result_sha256=result_sha256, error_class=error_class)
    except Exception:
        # Never print connection URL, driver exception or SQL credentials.
        raise RuntimeError("KMCO_DB_AUDIT_WRITE_FAILED") from None
    return "PERSISTED_UNVERIFIED"

