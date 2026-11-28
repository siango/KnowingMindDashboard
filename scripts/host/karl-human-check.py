#!/usr/bin/env python3
"""KARL human-visible, read-only AV15 acceptance probe.

No installation, checkout update, workflow dispatch, writes, secrets, or Codex.
Only authorized local MCP read calls; no circuit submit/DB mutation.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

OBSERVATIONS = []

REPO = "siango/KnowingMindDashboard"
BRANCH = "feature/kmcp-portable-circuits-20261122"
WORKFLOW = "kmcp-install-on-ubuntu.yml"
HOME = Path.home()
CHECKOUT = HOME / "kmcp-host-setup"
MARKER = HOME / "KnowingMind/.venvs/kmcp-portable/.knowingmind-managed.json"
PYTHON = HOME / "KnowingMind/.venvs/kmcp-portable/bin/python"
CFG = HOME / ".config/knowingmind/kmcw-owner-runtime.json"


def command(argv, *, timeout=20, input=None):
    try:
        return subprocess.run([str(a) for a in argv], capture_output=True,
                              input=input, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def emit(name, status, detail=""):
    OBSERVATIONS.append({"check_id": name, "status": str(status), "detail": str(detail)[:240]})
    print(f"{name:25} {status:12} {detail}", flush=True)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def response_error(obj):
    if "error" in obj:
        return True
    result = obj.get("result", {})
    return not isinstance(result, dict) or result.get("isError") is not False


def main():
    print("=== KARL HUMAN ACCEPTANCE / READ-ONLY ===", flush=True)
    print("OBSERVED_AT", datetime.now(timezone.utc).isoformat(), flush=True)
    print("MISSION G0-G1 / PR #6 / ubuntu-usb", flush=True)
    local_sha = None
    remote_sha = None
    installed_sha = None
    install_proved = False
    if (CHECKOUT / ".git").exists():
        v = command(["git", "-C", CHECKOUT, "rev-parse", "HEAD"])
        if v and v.returncode == 0:
            local_sha = v.stdout.strip()
    emit("LOCAL_SOURCE", "FOUND" if local_sha else "BLOCKED", (local_sha or "")[:12])
    v = command(["git", "-C", CHECKOUT, "rev-parse", "FETCH_HEAD"])
    if v and v.returncode == 0:
        remote_sha = v.stdout.strip()
    emit("FETCHED_SOURCE", "FOUND" if remote_sha else "UNVERIFIED", (remote_sha or "")[:12])
    marker = load_json(MARKER)
    if isinstance(marker, dict):
        installed_sha = marker.get("source_sha")
        emit("INSTALLED_MARKER", marker.get("state", "UNKNOWN"), str(installed_sha or "")[:12])
    else:
        emit("INSTALLED_MARKER", "NOT_FOUND")
    if remote_sha and installed_sha:
        install_proved = remote_sha == installed_sha and marker.get("state") == "INSTALLED"
        emit("INSTALLED_SHA_MATCH", "PASS" if install_proved else "DIFFERENT")
    else:
        emit("INSTALLED_SHA_MATCH", "UNVERIFIED")

    gh = command(["gh", "run", "list", "-R", REPO, "-w", WORKFLOW,
                  "-b", BRANCH, "-L", "20", "--json",
                  "databaseId,headSha,status,conclusion,event"], timeout=25)
    if not gh or gh.returncode:
        emit("GITHUB_ACTIONS", "UNAVAILABLE", "No run query available")
    else:
        try:
            runs = json.loads(gh.stdout)
            matching = [r for r in runs if r.get("headSha") == remote_sha] if remote_sha else []
            if matching:
                r = matching[0]
                emit("EXACT_SHA_RUN", str(r.get("status", "UNKNOWN")),
                     f"id={r.get('databaseId')} conclusion={r.get('conclusion')}")
                emit("RUN_LINK", "INFO", f"https://github.com/{REPO}/actions/runs/{r['databaseId']}")
            else:
                emit("EXACT_SHA_RUN", "NOT_FOUND", "in last 20 matching workflow runs")
        except ValueError:
            emit("GITHUB_ACTIONS", "INVALID_JSON")

    v = command(["pgrep", "-af", "[R]unner.Listener"])
    emit("RUNNER_PROCESS", "RUNNING" if v and v.returncode == 0 and v.stdout.strip() else "NOT_FOUND",
         "Process only; GitHub online status unverified")

    if not PYTHON.is_file() or not CFG.is_file():
        emit("LOCAL_MCP", "BLOCKED", "Installed Python or owner config missing")
    else:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "karl-human-check", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "kmco.inspect", "arguments": {"operation_id": "P01-M01"}}},
        ]
        payload = "".join(json.dumps(v) + "\n" for v in requests)
        v = command([PYTHON, "-I", "-m", "coding_worker_v2.agent.kmco_local_mcp",
                     "--config", CFG], input=payload, timeout=45)
        if not v or v.returncode != 0:
            emit("LOCAL_MCP", "BLOCKED", "Process unavailable/timeout/nonzero exit")
        else:
            try:
                replies = {}
                for line in v.stdout.splitlines():
                    try:
                        entry = json.loads(line)
                        if isinstance(entry, dict) and "id" in entry:
                            replies[entry["id"]] = entry
                    except ValueError:
                        continue
                emit("MCP_INITIALIZE", "PASS" if "result" in replies.get(1, {}) else "FAIL")
                tools = replies.get(2, {}).get("result", {}).get("tools")
                emit("MCP_TOOLS_LIST", "PASS" if isinstance(tools, list) else "FAIL",
                     f"count={len(tools)}" if isinstance(tools, list) else "")
                inspected = replies.get(3, {})
                emit("KMCO_INSPECT_P01", "PASS" if not response_error(inspected) else "FAIL")
            except (TypeError, AttributeError, KeyError, ValueError):
                emit("LOCAL_MCP", "INVALID_RESPONSE")

    emit("UNAUTHORIZED_DENY", "NOT_TESTED", "Read-only probe; use approved runner acceptance")
    emit("NATIVE_DB_RECEIPT", "NOT_TESTED", "Must come from existing runner artifact")
    emit("INDEPENDENT_QC", "NOT_VERIFIED")
    emit("AUTHORITY_SNAPSHOT", "NOT_VERIFIED")
    emit("PRODUCTION_GATE", "NO_GO", "No production promotion")
    print("=== KARL CHECK COMPLETE ===", flush=True)


def write_report(output):
    """Produce a sanitized projection for the existing receipt/artifact pipeline.
    Does not claim an independent QC or replace the KMCW receipt store.
    """
    import hashlib
    report = {
        "schema": "KARL_READ_ONLY_OBSERVATION_V1",
        "mission_id": "G0-G1",
        "pr": 6,
        "node_id": "ubuntu-usb",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "OBSERVATION_ONLY",
        "independent_qc": "NOT_VERIFIED",
        "production_authority": "NOT_GRANTED",
        "checks": OBSERVATIONS,
    }
    data = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path = Path(output)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("UNSAFE_REPORT_PATH")
    # Explicit opt-in by caller; no arbitrary default mutation of runner state.
    path.write_text(data, encoding="utf-8")
    print("EVIDENCE_PROJECTION_SHA256", hashlib.sha256(data.encode()).hexdigest(), flush=True)
    print("EVIDENCE_PROJECTION_PATH", path, flush=True)



def review_existing_circuit(result_path, config_path):
    """Read existing receipt and exact Core row; never execute or attest QC."""
    import hashlib
    import shutil
    import psycopg
    cfg = json.loads(Path(config_path).read_text())
    result = json.loads(Path(result_path).read_text())
    report = result["report"]
    assert report == result["result"]
    assert report["mission_id"] == cfg["mission_id"]
    assert report["status"] == "COMPLETED"
    node = report["nodes"]["p01"]
    assert node["status"] == "DONE"
    receipt = report["receipt"]
    root = Path(cfg["runtime_base"]).expanduser().resolve() / "receipts"
    p = Path(receipt["uri"]).resolve(strict=True)
    assert p.is_relative_to(root)
    assert hashlib.sha256(p.read_bytes()).hexdigest() == receipt["sha256"]
    assert node["result"]["output"]["state"] == "EXACT_QUOTE_VERIFIED"
    with psycopg.connect(service=cfg["postgres_service"], connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT current_database()")
            assert cur.fetchone() == ("km_microops",)
            cur.execute("""
                SELECT source_sha,result_state,result_sha256,dukkha,samudaya,nirodha,magga
                FROM circuit.core_memory WHERE mission_id=%s AND task_id=%s
                  AND operation=%s AND args_sha256=%s
            """, (cfg["mission_id"],cfg["task_id"],"kmco.p01.evidence",node["arguments_sha256"]))
            row = cur.fetchone()
            assert row is not None and cur.fetchone() is None
            assert row[0] == report["source_sha"]
            assert row[1] == "EXECUTED_UNVERIFIED"
            assert row[2] == node["result_sha256"]
            assert all(isinstance(v,dict) for v in row[3:])
    from coding_worker_v2.agent.kmco_core import recall
    recalled = recall({"mission_id":cfg["mission_id"],"task_id":cfg["task_id"],
                       "kmco_pg_service":cfg["postgres_service"]},limit=20)
    recalled_match = any(x["operation"] == "kmco.p01.evidence" and
                         x["result_sha256"] == node["result_sha256"] for x in recalled["cases"])
    disk = shutil.disk_usage("/srv/kebunate")
    return {"schema":"KARL_EXISTING_CIRCUIT_READBACK_V1",
            "observed_at":datetime.now(timezone.utc).isoformat(),
            "mission_id":cfg["mission_id"],"task_id":cfg["task_id"],
            "circuit_id":report["circuit_id"],"source_sha":report["source_sha"],
            "receipt_sha256":receipt["sha256"],"result_sha256":node["result_sha256"],
            "receipt_integrity":"PASS","core_exact_readback":"PASS",
            "core_recall":"PASS" if recalled_match else "NOT_IN_BOUNDED_RECALL",
            "execution_status":"COMPLETED_UNVERIFIED",
            "independent_qc":"NOT_ATTESTED","production_authority":"NOT_GRANTED",
            "resources":{"disk_total_bytes":disk.total,"disk_free_bytes":disk.free,
                         "load_average":list(os.getloadavg()),"provider_quota":"UNKNOWN"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", help="Optional JSON projection for existing workflow artifact upload")
    parser.add_argument("--review-circuit", type=Path)
    parser.add_argument("--config", type=Path, default=CFG)
    args = parser.parse_args()
    if args.review_circuit:
        report = review_existing_circuit(args.review_circuit, args.config)
        wire = json.dumps(report, ensure_ascii=False, indent=2)
        if args.json_out:
            with Path(args.json_out).open("x", encoding="utf-8") as stream:
                stream.write(wire+"\n")
        print(wire)
        raise SystemExit(0)
    main()
    if args.json_out:
        write_report(args.json_out)
