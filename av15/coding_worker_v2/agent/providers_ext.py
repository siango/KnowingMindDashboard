"""External proposal providers: authentication, least privilege, no nested Codex.

CLIs propose JSON edits; only the existing bounded pipeline applies them and
runs tests. A successful login is not a claim that inference will succeed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from ..providers import BaseProvider, Capabilities, Generation, ProviderError, redact

EXTERNAL = {"codex-cli", "copilot-cli", "copilot-cloud"}
USAGE_FILE_MAX_BYTES = 1_000_000


def _count(value):
    return value if type(value) is int and value >= 0 else None


def parse_copilot_usage(path: Path) -> dict:
    """Fail-closed parse of `copilot --usage-output-file` JSON. Never raises.

    Tokens are provider-reported counts. Premium requests and nano AIU are kept
    in their native units: no USD or token conversion contract exists.
    """
    unavailable = {"usage_source": "COPILOT_USAGE_UNAVAILABLE", "tokens_in": None,
                   "tokens_out": None, "premium_requests": None, "nano_aiu": None,
                   "billed_cost_usd": None}
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > USAGE_FILE_MAX_BYTES:
            return {**unavailable, "reason": "USAGE_FILE_MISSING_OR_INVALID"}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {**unavailable, "reason": "USAGE_FILE_UNREADABLE"}
    metrics = data.get("modelMetrics") if isinstance(data, dict) else None
    if not isinstance(metrics, dict) or not metrics:
        return {**unavailable, "reason": "USAGE_MODEL_METRICS_MISSING"}
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    models = []
    for model, row in metrics.items():
        usage = row.get("usage") if isinstance(row, dict) else None
        counts = {key: _count(usage.get(field)) if isinstance(usage, dict) else None
                  for key, field in (("input", "inputTokens"), ("output", "outputTokens"),
                                     ("cache_read", "cacheReadTokens"),
                                     ("cache_write", "cacheWriteTokens"))}
        if counts["input"] is None or counts["output"] is None:
            return {**unavailable, "reason": "USAGE_TOKEN_COUNTS_INVALID"}
        for key, value in counts.items():
            totals[key] += value or 0
        models.append(str(model)[:80])
    credits_complete = data.get("aiCreditsStatus") == "complete"
    return {"usage_source": "COPILOT_CLI_USAGE_OUTPUT_FILE",
            "tokens_in": totals["input"], "tokens_out": totals["output"],
            "cache_read_tokens": totals["cache_read"], "cache_write_tokens": totals["cache_write"],
            "models": models,
            "premium_requests": _count(data.get("totalPremiumRequestCost")) if credits_complete else None,
            "nano_aiu": _count(data.get("totalNanoAiu")) if credits_complete else None,
            "ai_credits_status": str(data.get("aiCreditsStatus"))[:40],
            "provider_request_id": None, "billed_cost_usd": None,
            "unit_contract": "NATIVE_UNITS_ONLY_NO_USD_CONVERSION"}


def supervised_by_codex() -> bool:
    if any(os.environ.get(k) for k in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "KMW2_CODEX_SUPERVISOR")):
        return True
    # Also catch a CLI parent if an intermediate launcher dropped its markers.
    pid = os.getppid()
    for _ in range(12):
        try:
            command = Path(f"/proc/{pid}/comm").read_text().strip().lower()
            if command.startswith("codex"):
                return True
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat[stat.rfind(')') + 2:].split()[1])
            if pid <= 1:
                break
        except (OSError, ValueError, IndexError):
            break
    return False


def probe_command(argv, timeout=20):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_bounded(argv, *, prompt, cwd, timeout, env):
    """Bounded process group; kill descendants on timeout, never invoke a shell."""
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+") as out, tempfile.TemporaryFile(mode="w+") as err:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=out,
                                stderr=err, text=True, env=env, start_new_session=True)
        try:
            proc.communicate(input=prompt, timeout=timeout)
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise
        out.seek(0); err.seek(0)
        stdout = out.read(256001)
        # Raw stderr is not returned: CLI diagnostics can contain credentials.
        return proc.returncode, stdout, int((time.monotonic() - started) * 1000)


class ProposalCli(BaseProvider):
    local = False
    executable = ""

    def __init__(self, config, *, authorized=False, workspace=None):
        super().__init__(config)
        self.authorized = authorized
        self.workspace = workspace or str(Path.cwd())

    def binary(self):
        return shutil.which(self.executable)

    def model_name(self):
        return os.environ.get(f"KMW2_{self.executable.upper()}_MODEL", "").strip() or f"{self.executable}-configured-default"

    def probe(self):
        binary = self.binary()
        report = {"provider": self.name, "installed": bool(binary), "authenticated": False,
                  "available": False, "model": self.model_name(), "reason": "BINARY_ABSENT"}
        if not binary:
            return report
        version = probe_command([binary, "--version"])
        if version is None:
            report["reason"] = "CLI_PROBE_TIMEOUT_OR_OS_ERROR"
            return report
        if version.returncode:
            report["reason"] = ("COPILOT_PLATFORM_PACKAGE_MISSING" if "no platform package found" in
                                (version.stdout + version.stderr) else "CLI_STARTUP_FAILED")
            report["exit_status"] = version.returncode
            return report
        report["version"] = re.sub(r"[^\w. -]", "", version.stdout.strip().splitlines()[0])[:100]
        auth = self.auth_probe(binary)
        report["authenticated"] = auth
        if not auth:
            report["reason"] = "AUTHENTICATION_UNAVAILABLE"
        elif self.name == "codex-cli" and supervised_by_codex():
            report["reason"] = "CODEX_SUPERVISOR_ACTIVE_RECURSION_BLOCKED"
        elif int(os.environ.get("KMW2_PROVIDER_DEPTH", "0") or 0) > 0:
            report["reason"] = "NESTED_PROVIDER_DISPATCH_BLOCKED"
        else:
            report.update(available=True, reason="AUTHENTICATED_INFERENCE_NOT_YET_VERIFIED")
        return report

    def auth_probe(self, binary):
        raise NotImplementedError

    def is_available(self):
        report = self.probe()
        if not report["available"]:
            return False, report["reason"]
        enabled = self.authorized or os.environ.get(f"KMW2_{self.executable.upper()}_ENABLED") == "1"
        return (True, report["reason"]) if enabled else (False, "EXTERNAL_NOT_AUTHORIZED")

    def capabilities(self):
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        return Capabilities(provider=self.name, model=self.model_name(), local=False)

    def generate(self, messages, max_tokens=1024, timeout=120):
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        prompt = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
        prompt += "\nReturn only the requested JSON edit proposal. Do not change files, invoke another AI, push, open a PR, or deploy."
        env = dict(os.environ, KMW2_PROVIDER_DEPTH="1", COPILOT_AUTO_UPDATE="false")
        try:
            with tempfile.TemporaryDirectory(prefix="km-proposal-") as directory:
                argv, last = self.argv(Path(directory))
                try:
                    rc, output, elapsed = run_bounded(argv, prompt=prompt, cwd=self.workspace,
                                                      timeout=timeout, env=env)
                finally:
                    usage = self.collect_usage(Path(directory))
                if rc:
                    raise ProviderError("CLI_EXEC_FAILED", f"provider={self.name} exit_status={rc} elapsed_ms={elapsed}",
                                        resources={"usage": usage})
                if last:
                    if not last.is_file() or last.stat().st_size > 256000:
                        raise ProviderError("INVALID_RESPONSE", "missing or oversized final response",
                                            resources={"usage": usage})
                    output = last.read_text(encoding="utf-8")
        except subprocess.TimeoutExpired:
            raise ProviderError("TIMEOUT", f"provider={self.name} timeout_s={timeout}") from None
        except OSError:
            raise ProviderError("CLI_EXEC_FAILED", f"provider={self.name} process start/read failed") from None
        if not output.strip() or len(output) > 256000:
            raise ProviderError("INVALID_RESPONSE", "empty or oversized CLI output",
                                resources={"usage": usage})
        return Generation(text=output, provider=self.name, model=self.model_name(), latency_ms=elapsed,
                          tokens_in=usage.get("tokens_in"), tokens_out=usage.get("tokens_out"),
                          execution={"exit_status": rc, "elapsed_ms": elapsed,
                                     "output_summary": redact(output[:500]), "mode": "read-only-edit-proposal",
                                     "usage_source": usage["usage_source"], "usage": usage,
                                     "provider_request_id": usage.get("provider_request_id")})

    def collect_usage(self, directory: Path) -> dict:
        return {"usage_source": "NOT_CAPTURED_BY_ADAPTER", "tokens_in": None,
                "tokens_out": None, "billed_cost_usd": None}


class CodexCliProvider(ProposalCli):
    name = "codex-cli"
    executable = "codex"

    def auth_probe(self, binary):
        result = probe_command([binary, "login", "status"])
        return result is not None and result.returncode == 0

    def argv(self, directory):
        last = directory / "final.json"
        argv = [self.binary(), "exec", "-s", "read-only", "--skip-git-repo-check",
                "--color", "never", "--json", "-o", str(last)]
        model = os.environ.get("KMW2_CODEX_MODEL", "").strip()
        if model:
            argv += ["-m", model]
        return argv + ["-"], last


class CopilotCliProvider(ProposalCli):
    name = "copilot-cli"
    executable = "copilot"

    def auth_probe(self, binary):
        # Reuse the existing GitHub credential broker, never extract its token.
        if not shutil.which("gh"):
            return False
        result = probe_command(["gh", "auth", "status"])
        return result is not None and result.returncode == 0

    def argv(self, directory):
        # Piped input is supported prompt mode. No shell/write/MCP/agent tools.
        argv = [self.binary(), "-s", "--no-color", "--no-auto-update",
                "--disable-builtin-mcps", "--available-tools=view,glob,grep",
                "--deny-tool=shell,write", "--max-autopilot-continues=0",
                "--usage-output-file", str(directory / "usage.json")]
        model = os.environ.get("KMW2_COPILOT_MODEL", "").strip()
        if model:
            argv += ["--model", model]
        return argv, None

    def collect_usage(self, directory):
        return parse_copilot_usage(directory / "usage.json")


def on_demand_chain(config, name):
    from ..providers import build_providers
    providers = build_providers(config)
    providers.update({"codex-cli": CodexCliProvider(config), "copilot-cli": CopilotCliProvider(config)})
    provider = providers.get(name)
    if provider is None:
        return []
    ok, _ = provider.is_available()
    return [provider] if ok else []
