"""V3 Phase 5: QC supervisor. Independent verification, risk-based depth.

Reuses pipeline checks, receipts validation, queue health, Ollama
inventory and gh CI readback. Failures yield a bounded repair cycle;
nothing is fabricated — inapplicable checks report SKIPPED.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import json
import hashlib
from pathlib import Path

from ..pipeline import syntax_check
from ..providers import http_json, require_loopback
from .sandbox import run_isolated
from .verification import SCHEMA as VERIFICATION_SCHEMA

SCHEMA = "KM_QC_REPORT_V1"
SECRET_ASSIGN_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]+")
SKIPPED = "SKIPPED"


class QCError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail[:300]}" if detail else reason)
        self.reason = reason
        self.detail = detail[:2000]


def _check(name: str, passed, detail: str = "") -> dict:
    return {"name": name, "passed": passed, "detail": str(detail)[:500]}


def risk_score(task, changed: list[str]) -> int:
    hints = {}
    try:
        hints = task.task_input.hints or {}
    except AttributeError:
        pass
    score = 0
    if hints.get("complexity") == "L":
        score += 2
    elif hints.get("complexity") == "M":
        score += 1
    if len(changed) > 3:
        score += 1
    try:
        if not task.task_input.test_command:
            score += 1
    except AttributeError:
        score += 1
    return score


def select_checks(score: int) -> list[str]:
    base = ["source_integrity", "requirements", "syntax", "security",
            "mission_audit", "independent_verification"]
    if score >= 3:
        return base + ["unit_tests", "integration_tests", "regression",
                       "runtime_health", "github_ci", "receipt_readback"]
    if score >= 1:
        return base + ["unit_tests", "integration_tests", "runtime_health",
                       "receipt_readback"]
    return base + ["unit_tests", "receipt_readback"]


def check_source_integrity(task, ctx: dict) -> dict:
    repo = ctx.get("repo", "")
    if not repo or not Path(repo).is_dir():
        return _check("source_integrity", False, "repo missing")
    if not shutil.which("git"):
        return _check("source_integrity", SKIPPED, "git unavailable")
    try:
        proc = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=15,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return _check("source_integrity", SKIPPED, "git probe failed")
    if proc.returncode != 0:
        return _check("source_integrity", SKIPPED, "git revision unavailable")
    revision = (proc.stdout or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        return _check("source_integrity", False, "invalid git revision")
    return _check("source_integrity", True, f"HEAD={revision}")


def check_requirements(task, ctx: dict) -> dict:
    _ = ctx
    try:
        hints = task.task_input.hints or {}
        acceptance = hints.get("acceptance", [])
    except AttributeError:
        return _check("requirements", False, "no task input")
    if not acceptance:
        return _check("requirements", SKIPPED, "no acceptance criteria recorded")
    changed = (task.output or {}).get("changed", [])
    test_runs = _task_tests(task)
    if not any(r.get("tests", {}).get("ran") for r in test_runs) or any(
            r.get("tests", {}).get("ran")
            and r["tests"].get("rc") != 0 for r in test_runs):
        return _check("requirements", False, "passing test evidence missing")
    if task.status == "DONE" and changed:
        return _check("requirements", True, f"{len(acceptance)} criteria, "
                                            f"{len(changed)} files, "
                                            f"{len(test_runs)} test runs")
    return _check("requirements", False,
                  f"status={task.status} changed={len(changed)}")


def check_syntax(task, ctx: dict) -> dict:
    repo = ctx.get("repo", "")
    changed = (task.output or {}).get("changed", [])
    if not changed:
        return _check("syntax", SKIPPED, "no changed files")
    result = syntax_check(repo, changed)
    if result["ok"]:
        return _check("syntax", True, f"checked={result['checked']}")
    return _check("syntax", False, "; ".join(result["errors"])[:400])


def _task_tests(task) -> list[dict]:
    return [e for e in (task.evidence or [])
            if isinstance(e, dict) and e.get("event") == "tests_run"]


def check_unit_tests(task, ctx: dict) -> dict:
    _ = ctx
    runs = _task_tests(task)
    if not runs or not any(r.get("tests", {}).get("ran") for r in runs):
        return _check("unit_tests", SKIPPED, "task ran no tests")
    bad = [r for r in runs if r.get("tests", {}).get("ran")
           and r["tests"].get("rc") != 0]
    if bad:
        return _check("unit_tests", False, f"{len(bad)} failing runs")
    return _check("unit_tests", True, f"{len(runs)} passing runs")


def check_integration_tests(task, ctx: dict) -> dict:
    repo = ctx.get("repo", "")
    try:
        command = task.task_input.test_command or ""
    except AttributeError:
        command = ""
    if not command:
        return _check("integration_tests", SKIPPED, "no test command")
    try:
        result = run_isolated(repo, command, 300)
    except Exception as exc:
        return _check("integration_tests", False, type(exc).__name__)
    if result.get("rc") == 0:
        detail = f"independent re-run rc=0 {result.get('duration_ms')}ms"
        base = ctx.get("workspace", "")
        if base and repo and Path(base).resolve() != Path(repo).resolve():
            detail += "; worktree isolated from base"
        return _check("integration_tests", True, detail)
    return _check("integration_tests", False,
                  f"re-run rc={result.get('rc')}")


def check_security(task, ctx: dict) -> dict:
    repo = ctx.get("repo", "")
    changed = (task.output or {}).get("changed", [])
    hits: list[str] = []
    for rel in changed[:20]:
        target = Path(repo) / rel
        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if SECRET_ASSIGN_RE.search(text):
            hits.append(f"{rel}:possible-secret")
        if target.suffix == ".py" and "shell=True" in text:
            hits.append(f"{rel}:shell=True")
    if hits:
        return _check("security", False, "; ".join(hits)[:400])
    return _check("security", True, f"scanned={min(len(changed), 20)}")


def check_regression(task, ctx: dict) -> dict:
    _ = task
    command = ctx.get("regression_command", "")
    repo = ctx.get("repo", "")
    if not command:
        return _check("regression", SKIPPED, "no regression command configured")
    try:
        result = run_isolated(repo, command, 600)
    except Exception as exc:
        return _check("regression", False, type(exc).__name__)
    if result.get("rc") == 0:
        return _check("regression", True, f"rc=0 {result.get('duration_ms')}ms")
    return _check("regression", False, f"rc={result.get('rc')}")


def check_runtime_health(task, ctx: dict) -> dict:
    _ = task
    store = ctx.get("store")
    config = ctx.get("config")
    if store is None or config is None:
        return _check("runtime_health", SKIPPED, "no store/config context")
    try:
        health = store.health()
    except Exception as exc:
        return _check("runtime_health", False, f"queue: {type(exc).__name__}")
    try:
        data = http_json(require_loopback(config.ollama_url) + "/api/tags",
                         timeout=10)
        models = len(data.get("models", []))
    except Exception as exc:
        return _check("runtime_health", False,
                      f"ollama: {getattr(exc, 'reason', type(exc).__name__)}")
    return _check("runtime_health", True,
                  f"queue={health} models={models}")


def check_github_ci(task, ctx: dict) -> dict:
    _ = task
    repo = ctx.get("repo", "")
    branch = ctx.get("branch", "")
    if not branch:
        return _check("github_ci", SKIPPED, "no branch context")
    if not shutil.which("gh"):
        return _check("github_ci", SKIPPED, "gh absent")
    try:
        proc = subprocess.run(["gh", "pr", "checks", "--required-only"],
                              cwd=repo, capture_output=True, text=True,
                              timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return _check("github_ci", SKIPPED, "gh probe failed")
    if proc.returncode != 0:
        return _check("github_ci", False, (proc.stdout + proc.stderr)[-400:])
    return _check("github_ci", True, f"branch={branch}")


def check_receipt_readback(task, ctx: dict) -> dict:
    receipt_path = ctx.get("receipt_path")
    if not receipt_path:
        return _check("receipt_readback", SKIPPED,
                      "durable receipt path unavailable")
    try:
        from ..receipts import read_receipt

        receipt = read_receipt(Path(receipt_path))
    except (OSError, ValueError):
        return _check("receipt_readback", False, "durable receipt unreadable")
    expected_source = (task.task_input.hints or {}).get("base_commit", "")
    expected_patch = (task.output or {}).get("patch_digest", "")
    expected_plan = (task.task_input.hints or {}).get("plan_digest", "")
    expected_scope = (task.task_input.hints or {}).get("scope_digest", "")
    lease_digest = (hashlib.sha256(task.lease_token.encode("utf-8")).hexdigest()
                    if task.lease_token else "")
    if (receipt.get("task_id") != task.task_id
            or receipt.get("source_sha", "") != expected_source
            or receipt.get("plan_digest", "") != expected_plan
            or receipt.get("scope_digest", "") != expected_scope
            or receipt.get("output", {}).get("patch_digest") != expected_patch
            or receipt.get("status") not in {"DONE", "QC_PENDING"}
            or not isinstance(receipt.get("receipt_version"), int)
            or receipt.get("lease_token_sha256") != lease_digest):
        return _check("receipt_readback", False,
                      "receipt identity, SHA, patch, or version mismatch")
    return _check("receipt_readback", True,
                  f"version={receipt['receipt_version']}")


def check_mission_audit(task, ctx: dict) -> dict:
    _ = task
    result = ctx.get("mission_audit")
    if not isinstance(result, dict):
        from .auditor import MissionAuditorError, run_mission_audit

        try:
            result = run_mission_audit("BEFORE_CLOSE", ctx)
        except MissionAuditorError as exc:
            return _check("mission_audit", False,
                          f"{exc.reason}: {exc.detail}")
    if result.get("gate_passed") is True:
        return _check("mission_audit", True,
                      f"stage={result.get('stage', 'BEFORE_CLOSE')}")
    return _check("mission_audit", False,
                  str(result.get("reason", "AUDIT_GATE_BLOCKED"))[:500])


def check_independent_verification(task, ctx: dict) -> dict:
    proof = ctx.get("independent_verification")
    if not isinstance(proof, dict):
        return _check("independent_verification", SKIPPED,
                      "independent verifier evidence unavailable")
    executor_id = str(ctx.get("executor_id") or task.owner_node)
    verifier_id = str(proof.get("verifier_id") or "")
    executor_runner = str(ctx.get("executor_runner_id") or "")
    verifier_runner = str(proof.get("runner_id") or "")
    if not verifier_id or not verifier_runner or not executor_runner:
        return _check("independent_verification", SKIPPED,
                      "executor/verifier identity or runner missing")
    if verifier_id == executor_id or verifier_runner == executor_runner:
        return _check("independent_verification", False,
                      "executor and verifier must be distinct")
    if proof.get("status") != "PASS":
        return _check("independent_verification", False,
                      "verifier did not report PASS")
    if (proof.get("authenticated") is not True
            or proof.get("schema") != VERIFICATION_SCHEMA
            or not re.fullmatch(r"[0-9a-f]{64}",
                                str(proof.get("signature_sha256", "")))):
        return _check("independent_verification", False,
                      "authenticated verifier signature missing")
    expected_sha = ((task.task_input.hints or {}).get("base_commit")
                    or (ctx.get("source_sha") or ""))
    source_sha = str(proof.get("source_sha") or "")
    patch_digest = str((task.output or {}).get("patch_digest") or "")
    hints = task.task_input.hints or {}
    approved_scope = hints.get("approved_scope", {})
    receipt_path = Path(str(ctx.get("receipt_path") or ""))
    try:
        receipt_raw = receipt_path.read_bytes()
        receipt = json.loads(receipt_raw)
    except (OSError, ValueError):
        return _check("independent_verification", False,
                      "provisional receipt readback missing")
    if (not re.fullmatch(r"[0-9a-fA-F]{40}", source_sha)
            or (expected_sha and source_sha != expected_sha)
            or not patch_digest or proof.get("patch_digest") != patch_digest
            or proof.get("task_id") != task.task_id
            or proof.get("mission_id") != task.mission_id
            or proof.get("plan_id") != hints.get("plan_id")
            or proof.get("plan_digest") != hints.get("plan_digest")
            or proof.get("scope_digest") != hints.get("scope_digest")
            or not isinstance(approved_scope, dict)
            or proof.get("allowed_paths") != approved_scope.get("allowed_paths")
            or proof.get("receipt_sha256") != hashlib.sha256(receipt_raw).hexdigest()
            or proof.get("receipt_version") != receipt.get("receipt_version")
            or proof.get("lease_token_sha256") != receipt.get("lease_token_sha256")
            or not proof.get("evidence_refs")):
        return _check("independent_verification", False,
                      "source, patch, or evidence reference mismatch")
    return _check("independent_verification", True,
                  f"verifier={verifier_id} runner={verifier_runner}")


CHECKS = {"source_integrity": check_source_integrity,
          "requirements": check_requirements, "syntax": check_syntax,
          "unit_tests": check_unit_tests,
          "integration_tests": check_integration_tests,
          "security": check_security, "regression": check_regression,
          "runtime_health": check_runtime_health,
          "github_ci": check_github_ci,
          "receipt_readback": check_receipt_readback,
          "mission_audit": check_mission_audit,
          "independent_verification": check_independent_verification}


def run_qc(task, ctx: dict, only: list[str] | None = None) -> dict:
    """Run selected checks. passed is False when any check fails."""
    changed = (task.output or {}).get("changed", [])
    score = risk_score(task, changed)
    selected = only or select_checks(score)
    results = []
    for name in selected:
        fn = CHECKS.get(name)
        if fn is None:
            results.append(_check(name, SKIPPED, "unknown check"))
            continue
        try:
            results.append(fn(task, ctx))
        except Exception as exc:
            results.append(_check(name, False, f"CRASH_{type(exc).__name__}"))
    failed = [r for r in results if r["passed"] is not True]
    return {"schema": SCHEMA, "task_id": getattr(task, "task_id", ""),
            "risk": score, "selected": selected,
            "checks": results, "passed": not failed,
            "failed": [r["name"] for r in failed],
            "unverified": [r["name"] for r in results
                           if r["passed"] == SKIPPED],
            "independent_verified": any(
                r["name"] == "independent_verification"
                and r["passed"] is True for r in results),
            "retries_used": int(ctx.get("qc_retries_used", 0))}


def qc_repair_cycle(task, ctx: dict, execute_repair_fn,
                    max_repairs: int = 1) -> dict:
    """QC -> bounded repair -> re-QC. Escalates when retries exhaust."""
    report = run_qc(task, ctx)
    repairs = 0
    while not report["passed"] and repairs < max_repairs:
        repairs += 1
        summary = "; ".join(f"{r['name']}:{r['detail']}" for r in report["checks"]
                            if r["passed"] is False)
        task = execute_repair_fn(task, summary[:2000])
        ctx = {**ctx, "qc_retries_used": repairs}
        affected = [r["name"] for r in report["checks"]
                    if r["passed"] is False]
        report = run_qc(task, ctx, only=affected)
        report["retries_used"] = repairs
    report["escalated"] = not report["passed"]
    report["next_action"] = ("QC_PASS" if report["passed"]
                             else "ESCALATE_AFTER_QC_REPAIRS")
    return report
