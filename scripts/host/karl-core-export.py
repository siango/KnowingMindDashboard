#!/usr/bin/env python3
"""Export existing KARL evidence index into KEBUNATE Core's existing ingest contract.

Only a sanitized, deterministic JSONL projection to kebunate.activity_event and
kebunate.problem_solution; no DB connection, mutation, authority or invented QC.
Schema: siango/kebunate/ops/data-plane/postgres/001_kebunate_operational_knowledge.sql
Writer: siango/kebunate/ops/data-plane/postgres/ingest_jsonl.py
"""
import argparse
import hashlib
import json
from pathlib import Path

SCHEMA = "KARL_RUN_EVIDENCE_INDEX_V1"
CHECK_STATES = {"PASS", "FAIL", "FAILED", "BLOCKED", "NOT_TESTED",
                "NOT_VERIFIED", "UNVERIFIED", "PRESENT", "MISSING_OR_UNREADABLE",
                "INVALID_JSON"}
STOP_STATES = {"BLOCKED_OR_FAILED", "INCOMPLETE"}
ALLOWED_EVENT_STATES = {"COLLECTED_UNVERIFIED", *STOP_STATES}


def key(prefix, *parts):
    wire = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(wire.encode()).hexdigest()[:32]


def convert(record):
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise ValueError("INDEX_SCHEMA_INVALID")
    if record.get("mission_id") != "G0-G1" or record.get("result") not in ALLOWED_EVENT_STATES:
        raise ValueError("MISSION_OR_STATUS_INVALID")
    sha = record.get("source_sha")
    if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("FULL_SOURCE_SHA_REQUIRED")
    run_id = str(record.get("run_id", ""))
    observed = record.get("recorded_at")
    if not run_id.isdigit() or not isinstance(observed, str) or not observed.endswith("+00:00"):
        raise ValueError("RUN_ID_OR_TIMESTAMP_INVALID")
    node = str(record.get("node", "UNKNOWN"))
    if not node or len(node) > 80 or not all(c.isalnum() or c in "_-" for c in node):
        raise ValueError("NODE_INVALID")
    checks = record.get("checks")
    if not isinstance(checks, dict) or set(checks) != {
        "installation", "handler_readback", "native_database", "local_mcp", "qualification"
    }:
        raise ValueError("CHECK_SET_INVALID")
    evidence = "github://siango/KnowingMindDashboard/actions/runs/" + run_id
    events = []
    for check_name, check in sorted(checks.items()):
        if not isinstance(check, dict) or check.get("state") not in {"PRESENT", "INVALID_JSON", "MISSING_OR_UNREADABLE"}:
            raise ValueError("CHECK_RECORD_INVALID")
        proof = check.get("sha256")
        if proof is not None and (not isinstance(proof, str) or len(proof) != 64
                                  or any(c not in "0123456789abcdef" for c in proof)):
            raise ValueError("DIGEST_INVALID")
        status = str(check.get("status", check["state"]))
        if len(status) > 100 or not status.replace("_", "").isalnum():
            raise ValueError("STATUS_INVALID")
        events.append({
            "event_id": key("KARL-", "G0-G1", sha, run_id, check_name, proof),
            "actor": "github-actions", "role": "runtime-evidence",
            "mission_id": "G0-G1", "node_id": node, "tools": check_name,
            "work_item": "KARL " + check_name,
            "recorded_at": observed,
            "duration_quality": "UNKNOWN", "status": status,
            "evidence_ref": evidence + "#check=" + check_name,
            "progress_summary": check["state"] + " / SOURCE_" + str(check.get("provenance", "UNVERIFIED")),
        })
    # A problem record may be created only for an observed failed/missing check,
    # with root cause unknown; an index is not diagnosis or a verified resolution.
    problems = []
    bad = [name for name, check in sorted(checks.items())
           if check.get("state") != "PRESENT" or check.get("status") in ("FAIL", "FAILED", "BLOCKED")
           or check.get("provenance") == "SOURCE_SHA_MISMATCH"]
    for name in bad:
        proof = checks[name].get("sha256", "")
        problems.append({
            "problem_id": key("KARL-P-", "G0-G1", sha, run_id, name, proof),
            "scope": "G0-G1", "node_id": node, "phase": name,
            "symptom": "KARL acceptance check reported non-success: " + name,
            "probe_or_script": "scripts/host/karl-evidence-index.py",
            "observed_evidence": evidence + "#check=" + name,
            "root_cause": "UNDETERMINED", "fix_or_workaround": "NOT_APPLIED",
            "verification": "INDEPENDENT_QC_NOT_VERIFIED",
            "baseline_version_target": sha, "reusable_detection_rule": "CHECK_EVIDENCE_AND_SHA",
            "auto_repair_policy": "DENY_UNTIL_OWNER_AUTHORITY",
            "evidence_ref": evidence + "#check=" + name,
            "status": "OPEN_UNVERIFIED", "updated_at": observed,
        })
    return events, problems


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()
    if args.index.is_symlink() or not args.index.is_file() or args.index.stat().st_size > 1024 * 1024:
        raise ValueError("INDEX_NOT_SAFE")
    events, problems = convert(json.loads(args.index.read_text(encoding="utf-8")))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("activity_event", events), ("problem_solution", problems)):
        out = args.out_dir / ("karl-core-" + name + ".jsonl")
        out.write_text("".join(json.dumps(v, sort_keys=True) + "\n" for v in rows), encoding="utf-8")
    print("KARL_CORE_EXPORT_ONLY", "ACTIVITY_EVENTS", len(events),
          "PROBLEM_CASES", len(problems), "DB_WRITES", 0,
          "PERSISTENT_READBACK", "NOT_VERIFIED")


if __name__ == "__main__":
    main()
