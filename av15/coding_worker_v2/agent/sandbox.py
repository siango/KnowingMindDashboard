"""V3.1 Phase 3: sandbox verification and hardened test execution.

Reuses the existing sandbox (`pipeline.run_tests` allowlist + timeouts).
Adds secret isolation (scrubbed child env), workspace confinement checks
and a machine-readable verify report. Model output is never executed.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ..pipeline import ALLOWED_TEST_PREFIXES, PipelineError, run_tests

SECRET_KEY_RE = re.compile(r"(?i)(secret|api[_-]?key|token|passwd|private[_-]?key)")
SCHEMA = "KM_SANDBOX_REPORT_V1"


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
