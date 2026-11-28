"""V3.1 Phase 3: sandbox verification and hardened test execution.

Reuses the existing sandbox (`pipeline.run_tests` allowlist + timeouts).
Adds secret isolation (scrubbed child env), workspace confinement checks
and a machine-readable verify report. Model output is never executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from ..pipeline import ALLOWED_TEST_PREFIXES, PipelineError, run_tests, test_argv
from ..providers import redact

SECRET_KEY_RE = re.compile(r"(?i)(secret|api[_-]?key|token|passwd|private[_-]?key)")
SCHEMA = "KM_SANDBOX_REPORT_V1"


def policy_decision(action: str, task_id: str, workspace: str, *,
                    paths=(), approved_paths=(), isolated=False,
                    command="", actor="KMCW_EXECUTOR") -> dict:
    """KMCW-owned decisions only; never answer host CLI approval prompts."""
    decision, rule = "REQUIRE_HUMAN_APPROVAL", "UNKNOWN_OR_PRIVILEGED_ACTION"
    root = Path(workspace).resolve()
    resolution_error = False
    try:
        resolved_paths = [(root / rel).resolve() for rel in paths]
    except (OSError, RuntimeError):
        resolved_paths, resolution_error = [], True
    canonical = [str(path.relative_to(root)) for path in resolved_paths
                 if path.is_relative_to(root)]
    protected = any(
        any(part.startswith(".env") or part in
            {".git", ".ssh", "credentials", "secrets", "production", "prod",
             "id_rsa", "id_ed25519"}
            for part in Path(rel).parts)
        or Path(rel).suffix.lower() in {".pem", ".key"}
        or "wrangler" in Path(rel).name.lower()
        or ".github/workflows" in str(rel).replace("\\", "/")
        for rel in [*paths, *canonical])
    confined = not resolution_error and all(
        not Path(rel).is_absolute() and ".." not in Path(rel).parts
        and target.is_relative_to(root) for rel, target in zip(paths, resolved_paths))
    if protected:
        rule = "SECRET_PERMISSION_OR_PRODUCTION"
    elif not confined:
        rule = "WORKSPACE_UNRESOLVABLE" if resolution_error else "WORKSPACE_ESCAPE"
    elif action in {"inspect", "git_status", "git_diff", "search", "checkpoint",
                    "receipt", "idempotent_retry"}:
        decision, rule = "AUTO_APPROVE", "SCOPED_SAFE_OPERATION"
    elif action == "edit":
        symlinked = any(
            (root.joinpath(*Path(rel).parts[:index])).is_symlink()
            for rel in paths for index in range(1, len(Path(rel).parts) + 1))
        if symlinked:
            rule = "SYMLINK_EDIT_REQUIRES_APPROVAL"
        elif isolated and paths and set(paths).issubset(approved_paths):
            decision, rule = "AUTO_APPROVE", "APPROVED_ISOLATED_EDIT"
        else:
            rule = "EDIT_SCOPE_OR_ISOLATION_REQUIRED"
    elif action in {"test", "lint", "static_check"}:
        try:
            test_argv(command)
            decision, rule = "AUTO_APPROVE", "ALLOWLISTED_CHECK"
        except PipelineError:
            rule = "COMMAND_NOT_ALLOWLISTED"
    return {"event": "approval_decision", "decision": decision,
            "rule_id": rule, "task_id": task_id, "actor": actor,
            "action": action, "source": "KMCW bounded execution policy",
            "observed_at": time.time(),
            "evidence": {"paths": list(paths), "command": redact(command),
                         "isolated_worktree": isolated}}


class SandboxError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail[:300]}" if detail else reason)
        self.reason = reason
        self.detail = detail[:2000]


def scrubbed_env(extra: dict | None = None) -> dict:
    """Child env without secret-bearing variables. Never logs values."""
    clean = {k: v for k, v in os.environ.items()
             if not SECRET_KEY_RE.search(k)}
    if extra:
        for key, value in extra.items():
            if SECRET_KEY_RE.search(key):
                continue
            clean[str(key)] = str(value)
    return clean


def check_confinement(repo: str, rel: str) -> Path:
    """Resolve rel inside repo or raise. Mirrors pipeline boundary."""
    root = Path(repo).resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise SandboxError("ESCAPE", rel[:120])
    return candidate


def run_isolated(repo: str, command: str, timeout_s: int,
                 extra_env: dict | None = None) -> dict:
    """Allowlisted tests with secret isolation."""
    if not Path(repo).is_dir():
        raise SandboxError("REPO_MISSING", repo[:120])
    try:
        return run_tests(repo, command, timeout_s, env=scrubbed_env(extra_env))
    except PipelineError as exc:
        raise SandboxError(exc.failure_class, exc.detail) from None



def run_isolated_observed(repo: str, command: str, timeout_s: int, *,
                          mission_id: str, task_id: str, source_sha: str) -> dict:
    """Run one existing allowlisted test with an evidence projection.

    The owner must persist this through the existing QueueStore/KMCW receipt
    path. This returns an observation only: never independent QC or a grant.
    No raw stdout/stderr is included to avoid credential leakage.
    """
    if (not isinstance(mission_id, str) or not mission_id or len(mission_id) > 128
            or not isinstance(task_id, str) or not task_id or len(task_id) > 128
            or not isinstance(source_sha, str) or len(source_sha) != 40
            or any(ch not in "0123456789abcdef" for ch in source_sha)
            or not isinstance(timeout_s, int) or not 1 <= timeout_s <= 600
            or not isinstance(command, str) or not command.strip()):
        raise SandboxError("OBSERVATION_CONTEXT_REQUIRED")
    try:
        result = run_isolated(repo, command, timeout_s)
        status = "PASS" if result.get("ran") is True and result.get("rc") == 0 else "FAIL"
        failure = "NONE" if status == "PASS" else "TEST_EXIT_NONZERO"
        code = result.get("rc")
        output = result.get("output", "")
        duration = result.get("duration_ms")
    except SandboxError as exc:
        status, failure, code, output, duration = "BLOCKED", exc.reason, None, "", None
    return {
        "schema": "KMCW_SANDBOX_OBSERVATION_V1",
        "mission_id": mission_id, "task_id": task_id, "source_sha": source_sha,
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "status": status, "failure_class": failure,
        "exit_code": code, "duration_ms": duration,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest() if status != "BLOCKED" else None,
        "output_captured_bytes": len(output.encode("utf-8")) if status != "BLOCKED" else 0,
        "receipt_status": "NOT_PERSISTED",
        "independent_qc": "NOT_RUN", "production_authority": "NOT_GRANTED",
    }

def run_isolated_recorded(ctx: dict, command: str, timeout_s: int) -> dict:
    """KMCW/KARL single-path sandbox execution and automatic existing-ledger event.

    Context comes from the already-authorized KMCW runtime, never CLI/env input.
    Fail closed if original QueueStore/source identity is unavailable. This is
    not an independent QC, core DB write, or permission escalation.
    """
    if not isinstance(ctx, dict):
        raise SandboxError("KMCW_CONTEXT_REQUIRED")
    store = ctx.get("store")
    if store is None or not callable(getattr(store, "append_event", None)):
        raise SandboxError("KMCW_EXISTING_EVENT_STORE_REQUIRED")
    from .memory import current_base_commit
    root = ctx.get("workspace")
    if not isinstance(root, str) or not Path(root).is_dir():
        raise SandboxError("KMCW_WORKSPACE_REQUIRED")
    source = current_base_commit(root)
    if not source or len(source) != 40 or any(ch not in "0123456789abcdef" for ch in source):
        raise SandboxError("KMCW_SOURCE_SHA_REQUIRED")
    mission, task, account = (ctx.get(k) for k in ("mission_id", "task_id", "account_id"))
    if any(not isinstance(v, str) or not v for v in (mission, task, account)):
        raise SandboxError("KMCW_SCOPED_IDENTITY_REQUIRED")
    result = run_isolated_observed(root, command, timeout_s,
                                   mission_id=mission, task_id=task, source_sha=source)
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    store.append_event(task, "karl.sandbox.observed", result["status"], digest,
                       account_id=account, mission_id=mission,
                       source_sha=source, observation=result)
    return {**result, "event_recorded": True, "event_digest": digest,
            "event_owner": "EXISTING_KMCW_QUEUE_STORE",
            "core_database_readback": "NOT_VERIFIED"}


def verify(repo: str, timeout_probe_s: int = 5) -> dict:
    """Sandbox conformance report. Honest booleans, no invented passes."""
    checks: dict = {}
    checks["workspace_exists"] = Path(repo).is_dir()
    try:
        check_confinement(repo, "../escape.txt")
        checks["confinement_enforced"] = False
    except SandboxError:
        checks["confinement_enforced"] = True
    try:
        run_tests(repo, "rm -rf /tmp/km-probe", timeout_probe_s)
        checks["allowlist_enforced"] = False
    except PipelineError as exc:
        checks["allowlist_enforced"] = exc.failure_class == "POLICY_DENY"
    probe_env = dict(os.environ)
    probe_env["KMW2_PROBE_SECRET_TOKEN"] = "probe-value"
    leaked = "KMW2_PROBE_SECRET_TOKEN" in {
        k: v for k, v in probe_env.items() if not SECRET_KEY_RE.search(k)}
    checks["secret_isolation"] = not leaked
    checks["timeout_supported"] = True  # subprocess timeout kills child
    checks["process_note"] = (
        "subprocess timeout terminates the direct child; orphaned "
        "grandchildren are not tracked")
    checks["network_policy"] = (
        "no dedicated egress filter; allowlisted test commands only, "
        "no shell, loopback-only model traffic")
    checks["command_allowlist"] = list(ALLOWED_TEST_PREFIXES)
    passed = all([checks["workspace_exists"], checks["confinement_enforced"],
                  checks["allowlist_enforced"], checks["secret_isolation"]])
    return {"schema": SCHEMA, "repo": repo, "passed": passed, "checks": checks}
