"""Read-only adapter for the canonical KM Mission Auditor source."""
from __future__ import annotations

import hashlib
import json
import os
import re
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
    if not isinstance(report, dict):
        return False
    matrix = report.get("matrix", [])
    if (report.get("schema") != "KM_MISSION_AUDIT_REPORT_V1"
            or report.get("requirements_total") != REQUIREMENT_COUNT
            or report.get("observed_requirements") != REQUIREMENT_COUNT
            or report.get("scope_version") != SCOPE_VERSION
            or report.get("scope_digest") != SCOPE_DIGEST
            or report.get("requirements_missing") != 0
            or report.get("global_findings")
            or not isinstance(matrix, list)
            or any(not isinstance(row, dict) for row in matrix)
            or len(matrix) != REQUIREMENT_COUNT
            or len({row.get("requirement_id") for row in matrix}) != REQUIREMENT_COUNT):
        return False
    blocked = {"BLOCKED", "SECURITY_BLOCKED", "STALE"}
    return not any(row.get("status") in blocked for row in matrix)


def context_digest(stage: str, context: dict) -> str:
    payload = {key: context.get(key) for key in (
        "mission_id", "plan_id", "revision", "owner_node", "source_command",
        "workspace", "plan_digest", "source_sha", "scope_digest", "task_id",
        "queue_task_id",
        "repo", "allowed_paths", "fingerprint", "patch_digest",
        "receipt_sha256", "receipt_version")}
    payload["stage"] = stage
    task = context.get("task")
    if isinstance(task, dict):
        payload["task"] = {key: task.get(key) for key in (
            "task_id", "goal", "allowed_paths", "acceptance")}
    tasks = context.get("tasks")
    if isinstance(tasks, list):
        payload["tasks"] = tasks
    snapshot = context.get("plan_snapshot")
    if isinstance(snapshot, dict):
        payload["plan_snapshot_digest"] = hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")).hexdigest()
    token = str(context.get("lease_token") or "")
    payload["lease_token_sha256"] = hashlib.sha256(
        token.encode("utf-8")).hexdigest() if token else ""
    tests = context.get("test_evidence")
    if isinstance(tests, list):
        payload["test_evidence"] = tests
    qc = context.get("qc_report")
    if isinstance(qc, dict):
        payload["qc_digest"] = hashlib.sha256(json.dumps(
            qc, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")).hexdigest()
    proof = context.get("independent_verification")
    if isinstance(proof, dict):
        payload["verification_digest"] = str(proof.get("signature_sha256", ""))
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()


def _validate_context(stage: str, context: dict | None) -> dict:
    if not isinstance(context, dict):
        raise MissionAuditorError("AUDIT_CONTEXT_INVALID", "context required")
    for key in ("mission_id", "plan_id", "owner_node", "source_command",
                "workspace"):
        if not isinstance(context.get(key), str) or not context[key].strip():
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID", key)
    if (type(context.get("revision")) is not int
            or context["revision"] < 1
            or not re.fullmatch(r"[0-9a-f]{64}",
                                str(context.get("plan_digest", "")))
            or context.get("scope_digest") != SCOPE_DIGEST
            or not re.fullmatch(r"[0-9a-fA-F]{40}",
                                str(context.get("source_sha", "")))):
        raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                  "revision, plan digest, or source SHA")
    workspace = Path(context["workspace"]).resolve()
    repo = Path(context.get("repo") or workspace).resolve()
    if not workspace.is_dir() or not repo.is_dir():
        raise MissionAuditorError("AUDIT_CONTEXT_INVALID", "workspace missing")
    if _git(repo, "rev-parse", "HEAD") != context["source_sha"]:
        raise MissionAuditorError("AUDIT_CONTEXT_INVALID", "stale source SHA")

    snapshot = context.get("plan_snapshot")
    if (not isinstance(snapshot, dict)
            or hashlib.sha256(json.dumps(
                snapshot, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode("utf-8")).hexdigest()
            != context["plan_digest"]
            or snapshot.get("mission_id") != context["mission_id"]
            or snapshot.get("plan_id") != context["plan_id"]
            or snapshot.get("revision") != context["revision"]
            or snapshot.get("owner_node") != context["owner_node"]
            or snapshot.get("source_command") != context["source_command"]
            or snapshot.get("tasks") != context.get("tasks")):
        raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                  "saved plan digest or identity mismatch")
    if stage == "ADMIT":
        tasks = context.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID", "plan tasks missing")
        ids = set()
        for task in tasks:
            paths = task.get("allowed_paths") if isinstance(task, dict) else None
            task_id = task.get("task_id") if isinstance(task, dict) else None
            if (not isinstance(task_id, str) or task_id in ids
                    or not isinstance(paths, list) or not paths
                    or any(not isinstance(path, str) for path in paths)
                    or len(paths) != len(set(paths))):
                raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                          "task identity or paths")
            ids.add(task_id)
            for rel in paths:
                if (not isinstance(rel, str) or not rel
                        or Path(rel).is_absolute()
                        or ".." in Path(rel).parts
                        or not (workspace / rel).resolve().is_relative_to(workspace)
                        or not (workspace / rel).resolve().is_file()):
                    raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                              "unsafe approved path")
        return context

    task = context.get("task")
    paths = context.get("allowed_paths")
    store = context.get("queue_store")
    task_id = context.get("queue_task_id")
    token = context.get("lease_token")
    if (not isinstance(task, dict) or not isinstance(paths, list) or not paths
            or any(not isinstance(path, str) for path in paths)
            or paths != task.get("allowed_paths")
            or not isinstance(task_id, str) or not task_id
            or not isinstance(token, str) or not token
            or store is None or not store.lease_valid(task_id, token)):
        raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                  "task scope or live lease missing")
    if not isinstance(snapshot, dict) or not any(
            isinstance(item, dict)
            and item.get("task_id") == task.get("task_id")
            and item.get("goal") == task.get("goal")
            and item.get("allowed_paths") == paths
            for item in snapshot.get("tasks", [])):
        raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                  "task not in approved plan")
    for rel in paths:
        target = (repo / rel).resolve()
        if (Path(rel).is_absolute() or ".." in Path(rel).parts
                or not target.is_relative_to(repo) or not target.is_file()):
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                      "approved path changed or escaped")
    if not re.fullmatch(r"[0-9a-fA-F]{64}",
                        str(context.get("fingerprint", ""))):
        raise MissionAuditorError("AUDIT_CONTEXT_INVALID", "fingerprint missing")
    if stage in {"AFTER_TEST", "BEFORE_CLOSE"}:
        tests = context.get("test_evidence")
        if (not isinstance(tests, list) or not any(
                isinstance(event, dict) and event.get("event") == "tests_run"
                and event.get("tests", {}).get("ran") is True
                and event.get("tests", {}).get("rc") == 0 for event in tests)):
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                      "passing test evidence missing")
        if not re.fullmatch(r"[0-9a-f]{64}",
                            str(context.get("patch_digest", ""))):
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                      "patch digest missing")
    if stage == "BEFORE_CLOSE":
        receipt_path = Path(str(context.get("receipt_path") or ""))
        qc = context.get("qc_report")
        proof = context.get("independent_verification")
        if (not receipt_path.is_file()
                or not isinstance(qc, dict) or qc.get("passed") is not True
                or qc.get("independent_verified") is not True
                or not isinstance(proof, dict)
                or proof.get("authenticated") is not True):
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                      "receipt, passing QC, or signed verifier proof missing")
        checks = qc.get("checks")
        required_checks = {"requirements", "syntax", "security", "unit_tests",
                           "mission_audit", "independent_verification",
                           "receipt_readback"}
        if (not isinstance(checks, list)
                or any(not isinstance(check, dict)
                       or check.get("passed") is not True for check in checks)
                or not required_checks.issubset({
                    check.get("name") for check in checks
                    if isinstance(check, dict)})):
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                      "mandatory QC evidence missing or skipped")
        try:
            raw = receipt_path.read_bytes()
            receipt = json.loads(raw)
        except (OSError, ValueError):
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                      "receipt unreadable") from None
        if (hashlib.sha256(raw).hexdigest() != context.get("receipt_sha256")
                or receipt.get("task_id") != task_id
                or receipt.get("source_sha") != context["source_sha"]
                or (receipt.get("output") or {}).get("patch_digest")
                != context["patch_digest"]
                or receipt.get("status") != "QC_PENDING"
                or not isinstance(receipt.get("receipt_version"), int)):
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                      "receipt identity or digest mismatch")
        evidence = receipt.get("evidence", [])
        if (not any(isinstance(event, dict)
                    and event.get("event") == "qc_checked"
                    and event.get("qc", {}).get("passed") is True
                    for event in evidence)
                or not any(isinstance(event, dict)
                           and event.get("event") == "independent_verification"
                           and event.get("proof", {}).get("signature_sha256")
                           == proof.get("signature_sha256")
                           for event in evidence)):
            raise MissionAuditorError("AUDIT_CONTEXT_INVALID",
                                      "receipt lacks QC or verifier readback")
    return context


def run_mission_audit(stage: str, context: dict | None = None) -> dict:
    """Run the pinned auditor against its existing checkout and optional snapshot."""
    if stage not in STAGES:
        raise MissionAuditorError("AUDIT_STAGE_INVALID", stage)
    context = _validate_context(stage, context)
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
            "context_digest": context_digest(stage, context), "report": report}
