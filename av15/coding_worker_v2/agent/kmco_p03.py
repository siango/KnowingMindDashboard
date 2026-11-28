"""KMCO P03-M01..M05: deterministic, bounded retry loop breaker.

Accepts caller-supplied immutable attempt observations. The handler returns
a fail-closed decision only; it never changes a queue lease, retries a task,
grants authority or claims that supplied observations are independently verified.
"""
from __future__ import annotations

import hashlib
import json
import re

SHA = re.compile(r"^[0-9a-f]{64}$")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class P03Blocked(ValueError):
    pass


def _attempts(items, *, mission_id, task_id):
    if not ID.fullmatch(mission_id or "") or not ID.fullmatch(task_id or ""):
        raise P03Blocked("P03_MISSION_TASK_INVALID")
    if not isinstance(items, list) or len(items) > 12:
        raise P03Blocked("P03_ATTEMPTS_OUT_OF_RANGE")
    previous = 0
    normalized = []
    for row in items:
        if not isinstance(row, dict) or set(row) != {
            "attempt", "mission_id", "task_id", "failure_class",
            "source_sha256", "progress_units", "terminal"
        }:
            raise P03Blocked("P03_ATTEMPT_SCHEMA_INVALID")
        if (row["mission_id"] != mission_id or row["task_id"] != task_id
                or type(row["attempt"]) is not int or row["attempt"] != previous + 1
                or type(row["progress_units"]) is not int
                or not 0 <= row["progress_units"] <= 1000000
                or type(row["terminal"]) is not bool
                or not isinstance(row["failure_class"], str)
                or len(row["failure_class"]) > 96
                or not re.fullmatch(r"[A-Z][A-Z0-9_]*", row["failure_class"])
                or not isinstance(row["source_sha256"], str)
                or not SHA.fullmatch(row["source_sha256"])):
            raise P03Blocked("P03_ATTEMPT_SCOPE_OR_ORDER_INVALID")
        normalized.append(row)
        previous = row["attempt"]
    return normalized


def attempt_counter(items, *, mission_id, task_id):
    """P03-M01: count bounded, strictly sequential attempt snapshots."""
    rows = _attempts(items, mission_id=mission_id, task_id=task_id)
    return {"attempts": len(rows), "max_observed": 12}


def failure_fingerprint(items, *, mission_id, task_id):
    """P03-M02: privacy-safe repetition key, not an identity or grant."""
    rows = _attempts(items, mission_id=mission_id, task_id=task_id)
    return [{"attempt": r["attempt"], "digest": hashlib.sha256(
        json.dumps([r["source_sha256"], r["failure_class"]],
                   separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()}
        for r in rows]


def progress_delta(items, *, mission_id, task_id):
    """P03-M03: monotonically observed progress, never inferred improvement."""
    rows = _attempts(items, mission_id=mission_id, task_id=task_id)
    if not rows:
        return {"units_delta": 0, "regressions": 0}
    return {"units_delta": rows[-1]["progress_units"] - rows[0]["progress_units"],
            "regressions": sum(b["progress_units"] < a["progress_units"]
                               for a, b in zip(rows, rows[1:]))}


def repeat_detector(items, *, mission_id, task_id):
    """P03-M04: consecutive identical failure fingerprints with no progress."""
    rows = _attempts(items, mission_id=mission_id, task_id=task_id)
    keys = failure_fingerprint(items, mission_id=mission_id, task_id=task_id)
    repetitions = 0
    for i in range(len(rows)-1, 0, -1):
        if (keys[i]["digest"] == keys[i-1]["digest"]
                and rows[i]["progress_units"] <= rows[i-1]["progress_units"]):
            repetitions += 1
        else:
            break
    return {"consecutive_repeats": repetitions, "same_failure_without_progress": repetitions >= 2}


def loop_circuit_breaker(items, *, mission_id, task_id, max_attempts=3):
    """P03-M05: proposed stop/continue only, never an automatic queue action."""
    if type(max_attempts) is not int or not 1 <= max_attempts <= 12:
        raise P03Blocked("P03_RETRY_BUDGET_INVALID")
    rows = _attempts(items, mission_id=mission_id, task_id=task_id)
    repeat = repeat_detector(items, mission_id=mission_id, task_id=task_id)
    delta = progress_delta(items, mission_id=mission_id, task_id=task_id)
    terminal = bool(rows and rows[-1]["terminal"])
    exhausted = len(rows) >= max_attempts
    stop = terminal or exhausted or repeat["same_failure_without_progress"] or delta["regressions"] > 0
    return {
        "circuit": "P03", "status": "STOP_RECOMMENDED" if stop else "RETRY_ELIGIBILITY_UNVERIFIED",
        "stop": stop, "reason": ("TERMINAL_OBSERVED" if terminal else
                               "RETRY_BUDGET_EXHAUSTED" if exhausted else
                               "IDENTICAL_FAILURE_NO_PROGRESS" if repeat["same_failure_without_progress"] else
                               "PROGRESS_REGRESSION" if delta["regressions"] else
                               "INSUFFICIENT_EVIDENCE_TO_AUTHORIZE_RETRY"),
        "observed_attempts": len(rows),
        "consecutive_repeats": repeat["consecutive_repeats"],
        "progress": delta,
        "steps_completed": [f"P03-M{i:02d}" for i in range(1,6)],
        "queue_mutated": False, "retry_authorized": False,
        "evidence_verified": False, "independent_qc": "NOT_RUN",
        "source": "CALLER_SUPPLIED_OBSERVATIONS"
    }


def run_p03(items, *, mission_id, task_id, max_attempts=3):
    attempt_counter(items, mission_id=mission_id, task_id=task_id)
    failure_fingerprint(items, mission_id=mission_id, task_id=task_id)
    progress_delta(items, mission_id=mission_id, task_id=task_id)
    repeat_detector(items, mission_id=mission_id, task_id=task_id)
    return loop_circuit_breaker(items, mission_id=mission_id, task_id=task_id,
                                max_attempts=max_attempts)
