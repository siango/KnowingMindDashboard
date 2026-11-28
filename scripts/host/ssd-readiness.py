#!/usr/bin/env python3
"""Read-only KEBUNATE SSD/USB host readiness and security gate.

No secret reads, shell commands, token issuance, service mutation, DB writes,
runner registration, package install, or remote calls. Host-local evidence only.
A PASS here is NOT independent QC, production authority, or E2E acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
NODES = {"ubuntu-usb", "ubuntu-ssd"}

def probe(argv, timeout=4):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, ""

def inspect(home: Path, node: str, source_sha: str | None = None):
    if node not in NODES:
        raise ValueError("INVALID_NODE")
    if source_sha is not None and not SHA.fullmatch(source_sha):
        raise ValueError("INVALID_EXPECTED_SHA")
    home = home.expanduser().resolve()
    checks = {}
    def add(name, state, reason):
        checks[name] = {"state": state, "reason": reason}
    if not home.is_dir():
        add("home", "BLOCKED", "HOME_NOT_FOUND")
        free = None
    else:
        free = shutil.disk_usage(home).free
        add("home", "PASS", "DIRECTORY_PRESENT")
    add("disk_space", "PASS" if free is not None and free > 1024**3 else "BLOCKED",
        "MORE_THAN_1_GIB_FREE" if free is not None and free > 1024**3 else "INSUFFICIENT_OR_UNAVAILABLE")
    checkout = home / "kmcp-host-setup"
    if (checkout / ".git").exists():
        status, value = probe(["git", "-C", str(checkout), "rev-parse", "HEAD"])
        local_sha = value if status == 0 and SHA.fullmatch(value) else None
    else:
        local_sha = None
    add("source_checkout", "PASS" if local_sha else "BLOCKED",
        "GIT_SHA_PRESENT" if local_sha else "CHECKOUT_OR_SHA_MISSING")
    marker = home / "KnowingMind/.venvs/kmcp-portable/.knowingmind-managed.json"
    installed_sha = None
    if marker.is_file() and not marker.is_symlink():
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            if (value.get("state") == "INSTALLED" and
                    SHA.fullmatch(str(value.get("source_sha", "")))):
                installed_sha = value["source_sha"]
        except (ValueError, OSError, TypeError):
            pass
    add("installed_package", "PASS" if installed_sha else "BLOCKED",
        "MANAGED_INSTALL_MARKER_PRESENT" if installed_sha else "INSTALLED_MARKER_UNVERIFIED")
    same = bool(local_sha and installed_sha and local_sha == installed_sha and
                (source_sha is None or installed_sha == source_sha))
    add("source_alignment", "PASS" if same else "BLOCKED", "EXACT_SHA_MATCH" if same else "SOURCE_DRIFT_OR_NOT_PROVEN")
    profile = home / ".config/knowingmind/kmcw-owner-runtime.json"
    secure = False
    if profile.is_file() and not profile.is_symlink():
        mode = profile.stat().st_mode
        secure = profile.stat().st_uid == os.getuid() and not mode & (stat.S_IRWXG | stat.S_IRWXO)
    add("owner_config", "PASS" if secure else "BLOCKED",
        "PRESENT_OWNER_ONLY_MODE" if secure else "MISSING_OR_UNSAFE_METADATA")
    runner = home / "KnowingMind/runners/github/.runner"
    add("runner_registration", "PASS" if runner.is_file() and not runner.is_symlink() else "BLOCKED",
        "LOCAL_REGISTRATION_FILE_PRESENT_NOT_ONLINE_PROOF"
        if runner.is_file() and not runner.is_symlink() else "RUNNER_REGISTRATION_NOT_FOUND")
    rc, _ = probe(["pg_isready", "-q"])
    add("postgres_connectivity", "PASS" if rc == 0 else "BLOCKED",
        "LOCAL_PG_ACCEPTS_CONNECTIONS_NOT_DB_READBACK" if rc == 0 else "PG_ISREADY_NOT_PASS")
    rc, _ = probe(["systemctl", "is-active", "--quiet", "kmcp-v5.service"])
    add("kmcp_service", "PASS" if rc == 0 else "BLOCKED",
        "SYSTEMD_ACTIVE_NOT_MCP_CALL_PROOF" if rc == 0 else "SERVICE_NOT_ACTIVE_OR_UNAVAILABLE")
    return {
        "schema": "KEBUNATE_HOST_READINESS_V1",
        "mission": "G0-G1",
        "node": node,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": local_sha,
        "installed_sha": installed_sha,
        "expected_sha": source_sha,
        "free_bytes": free,
        "checks": checks,
        "status": "HOST_PRECHECK_PASS_NOT_PRODUCTION_ACCEPTANCE"
                  if all(x["state"] == "PASS" for x in checks.values()) else "HOST_PRECHECK_BLOCKED",
        "independent_qc": "NOT_PERFORMED",
        "http_origin_acceptance": "NOT_PERFORMED",
        "database_readback": "NOT_PERFORMED",
        "production_authority": "NOT_GRANTED",
        "mutations": 0,
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node", required=True, choices=sorted(NODES))
    ap.add_argument("--home", type=Path, default=Path.home())
    ap.add_argument("--expected-sha")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    report = inspect(args.home, args.node, args.expected_sha)
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        if args.output.is_symlink() or (args.output.exists() and not args.output.is_file()):
            ap.error("UNSAFE_OUTPUT_PATH")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "HOST_PRECHECK_PASS_NOT_PRODUCTION_ACCEPTANCE" else 2

if __name__ == "__main__":
    raise SystemExit(main())
