"""Read-only adapter for the canonical KM Mission Auditor source."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

AUDITOR_COMMIT = "e47b14343e4d498860706132288e61039cef372a"
AUDITOR_BLOB = "4b987d4ebae53122c14e6de743d2e96afcad7a8b"
MANIFEST_BLOB = "bf66324f07cde1b31fa7adcd99b76023ad2a71cc"
SCOPE_DIGEST = "sha256:c81a571e9f7437846f5bcc5c1db1096e04e22128b20ecda85e10f5b7d906a8e0"
REQUIREMENT_COUNT = 31
SCOPE_VERSION = 2
STAGES = {"ADMIT", "BEFORE_MUTATION", "AFTER_TEST", "BEFORE_CLOSE"}


class MissionAuditorError(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail[:300]}" if detail else reason)
        self.reason = reason
        self.detail = detail[:2000]


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=15,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MissionAuditorError("AUDITOR_UNAVAILABLE",
                                  f"git probe {type(exc).__name__}") from None
    if proc.returncode:
        raise MissionAuditorError("AUDITOR_UNAVAILABLE",
                                  (proc.stderr or proc.stdout or "git probe failed")[:300])
    return (proc.stdout or "").strip()


def _blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _gate_passed(report: dict) -> bool:
    matrix = report.get("matrix", [])
    if (report.get("schema") != "KM_MISSION_AUDIT_REPORT_V1"
            or report.get("requirements_total") != REQUIREMENT_COUNT
            or report.get("observed_requirements") != REQUIREMENT_COUNT
            or report.get("scope_version") != SCOPE_VERSION
            or report.get("scope_digest") != SCOPE_DIGEST
            or report.get("requirements_missing") != 0
            or report.get("global_findings")
            or len(matrix) != REQUIREMENT_COUNT
            or len({row.get("requirement_id") for row in matrix}) != REQUIREMENT_COUNT):
        return False
    blocked = {"BLOCKED", "SECURITY_BLOCKED", "STALE"}
    return not any(row.get("status") in blocked for row in matrix)


def run_mission_audit(stage: str, context: dict | None = None) -> dict:
    """Run the pinned auditor against its existing checkout and optional snapshot."""
    if stage not in STAGES:
        raise MissionAuditorError("AUDIT_STAGE_INVALID", stage)
    _ = context
    configured_root = os.environ.get("KMW2_MISSION_AUDITOR_ROOT", "")
    if not configured_root:
        raise MissionAuditorError("AUDITOR_UNAVAILABLE",
                                  "KMW2_MISSION_AUDITOR_ROOT is unset")
    root = Path(configured_root).resolve()
    if not root.is_dir():
        raise MissionAuditorError("AUDITOR_UNAVAILABLE", "source checkout missing")
    head = _git(root, "rev-parse", "HEAD")
    if head != AUDITOR_COMMIT:
        raise MissionAuditorError("AUDITOR_UNAVAILABLE",
                                  f"source SHA mismatch: {head[:12]}")
    script = root / "scripts/control/km_mission_auditor.py"
    manifest = root / "docs/control/KM_UNIFIED_MISSION_ACCEPTANCE_V1.json"
    if (not script.is_file() or not manifest.is_file()
            or _blob_sha(script) != AUDITOR_BLOB
            or _blob_sha(manifest) != MANIFEST_BLOB):
        raise MissionAuditorError("AUDITOR_UNAVAILABLE",
                                  "pinned source or manifest digest mismatch")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MissionAuditorError("AUDITOR_UNAVAILABLE", "manifest unreadable") from None
    if (data.get("scope_version") != SCOPE_VERSION
            or len(data.get("requirements", [])) != REQUIREMENT_COUNT):
        raise MissionAuditorError("AUDITOR_UNAVAILABLE",
                                  "canonical scope contract mismatch")

    snapshot = os.environ.get("KMW2_MISSION_AUDITOR_SNAPSHOT", "")
    with tempfile.TemporaryDirectory(prefix="kmcw-audit-") as tmp:
        report_path = Path(tmp) / "report.json"
        command = [sys.executable, str(script), "--manifest", str(manifest),
                   "--out", str(report_path)]
        if snapshot:
            snapshot_path = Path(snapshot).resolve()
            if not snapshot_path.is_file():
                raise MissionAuditorError("AUDITOR_UNAVAILABLE",
                                          "authority snapshot missing")
            command += ["--snapshot", str(snapshot_path)]
        try:
            proc = subprocess.run(command, cwd=root, capture_output=True,
                                  text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MissionAuditorError("AUDITOR_UNAVAILABLE",
                                      f"auditor process {type(exc).__name__}") from None
        if proc.returncode != 0:
            raise MissionAuditorError(
                "AUDITOR_FAILED", (proc.stdout + proc.stderr)[-1000:])
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise MissionAuditorError("AUDITOR_FAILED", "report unreadable") from None
    return {"stage": stage, "gate_passed": _gate_passed(report),
            "report": report}
