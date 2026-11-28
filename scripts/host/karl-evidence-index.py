#!/usr/bin/env python3
"""Index existing ubuntu-usb acceptance reports into one sanitized GitHub artifact.

This is a projection, not a replacement for KMCW's canonical versioned receipts.
Never dispatch work, modify owner config, read secrets, or fabricate a PASS.
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPORTS = {
    "installation": "kmcp-ubuntu-installation.json",
    "handler_readback": "kmco-installed-handler-readback.json",
    "native_database": "kmco-native-database-canary.json",
    "local_mcp": "kmco-local-tools-call.json",
    "qualification": "kmco-incremental-qualification.json",
}
# Only explicitly permitted fields can leave the host; never copy whole reports.
FIELDS = ("schema", "status", "source_sha", "node", "observed_at",
          "failure_class", "blocker", "production_acceptance", "independent_qc",
          "http_acceptance", "unauthorized_deny", "readback_row_count")
MAX_SIZE = 1024 * 1024


def entry(root, name, source_sha):
    file = root / name
    if not file.is_file() or file.is_symlink() or file.stat().st_size > MAX_SIZE:
        return {"state": "MISSING_OR_UNREADABLE"}
    data = file.read_bytes()
    result = {"state": "PRESENT", "sha256": hashlib.sha256(data).hexdigest()}
    try:
        report = json.loads(data)
        if not isinstance(report, dict):
            raise ValueError("NOT_OBJECT")
        for key in FIELDS:
            v = report.get(key)
            if key in ("blocker", "failure_class"):
                # Never export free-text exceptions or potentially sensitive detail.
                if isinstance(v, str):
                    result[key + "_sha256"] = hashlib.sha256(v.encode()).hexdigest()
                continue
            if isinstance(v, (bool, int)) or (isinstance(v, str) and len(v) <= 120):
                result[key] = v
        if report.get("source_sha") and report["source_sha"] != source_sha:
            result["provenance"] = "SOURCE_SHA_MISMATCH"
        elif report.get("source_sha") == source_sha:
            result["provenance"] = "SOURCE_SHA_MATCH"
        else:
            result["provenance"] = "UNVERIFIED"
    except (ValueError, UnicodeDecodeError, TypeError):
        result["state"] = "INVALID_JSON"
    return result


def build(root, sha, run_id, job_id):
    if not (len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)):
        raise ValueError("FULL_SOURCE_SHA_REQUIRED")
    checks = {label: entry(root, filename, sha) for label, filename in REPORTS.items()}
    missing = [name for name, v in checks.items() if v["state"] != "PRESENT"]
    discrepancies = [name for name, v in checks.items()
                     if v.get("provenance") == "SOURCE_SHA_MISMATCH"]
    problems = [name for name, v in checks.items()
                if v.get("status") in ("BLOCKED", "FAILED", "FAIL")
                or v["state"] == "INVALID_JSON"]
    if discrepancies or problems:
        state = "BLOCKED_OR_FAILED"
    elif missing:
        state = "INCOMPLETE"
    else:
        state = "COLLECTED_UNVERIFIED"
    return {
        "schema": "KARL_RUN_EVIDENCE_INDEX_V1",
        "role": "READ_ONLY_PROJECTION_NOT_AUTHORITY",
        "mission_id": "G0-G1",
        "source_sha": sha,
        "run_id": str(run_id),
        "job_id": str(job_id),
        "node": os.environ.get("RUNNER_NAME", "UNKNOWN")[:80],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "result": state,
        "failed_checks": problems,
        "missing_checks": missing,
        "sha_mismatch_checks": discrepancies,
        "checks": checks,
        "independent_qc": "NOT_VERIFIED",
        "production_authority": "NOT_GRANTED",
        "canonical_receipts": "EXISTING_KMCW_RECEIPT_HISTORY",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reports-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--job-id", default="")
    args = p.parse_args()
    result = build(args.reports_dir, args.source_sha, args.run_id, args.job_id)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("KARL_INDEX", result["result"], "RUN", args.run_id,
          "BLOCKERS", ",".join(result["failed_checks"] + result["sha_mismatch_checks"]),
          "MISSING", ",".join(result["missing_checks"]))


if __name__ == "__main__":
    main()
