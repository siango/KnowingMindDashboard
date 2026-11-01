"""V3.1 Phase 4: optional external providers. Disabled by default.

Codex CLI is wired read-only (sandbox read-only, output treated as data,
never executed). Copilot CLI has no working binary in this runtime
(`no platform package found`), so no adapter is shipped for it — see the
Phase 0 report. External spend is NOT metered here; enabling any external
provider requires an explicit flag and remains the owner's billing scope.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

from ..providers import (BaseProvider, Capabilities, Generation, ProviderError,
                         redact)


class CodexCliProvider(BaseProvider):
    name = "codex-cli"
    local = False

    def binary(self) -> str | None:
        return shutil.which("codex")

    def enabled(self) -> bool:
        return os.environ.get("KMW2_CODEX_ENABLED", "").strip() == "1"

    def model_name(self) -> str:
        return os.environ.get("KMW2_CODEX_MODEL", "").strip() or "codex-default"

    def is_available(self) -> tuple[bool, str]:
        if not self.enabled():
            return False, "CODEX_NOT_ENABLED"
        binary = self.binary()
        if not binary:
            return False, "CODEX_BINARY_ABSENT"
        try:
            proc = subprocess.run([binary, "--version"], capture_output=True,
                                  text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False, "CODEX_PROBE_FAIL"
        if proc.returncode != 0:
            return False, "CODEX_PROBE_FAIL"
        return True, f"MODEL={self.model_name()}"

    def capabilities(self) -> Capabilities:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        return Capabilities(provider=self.name, model=self.model_name(),
                            local=False)

    def generate(self, messages: list[dict], max_tokens: int = 1024,
                 timeout: int = 120) -> Generation:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        binary = self.binary()
        assert binary
        prompt = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}"
                             for m in messages if isinstance(m, dict))
        started = time.monotonic()
        with tempfile.NamedTemporaryFile("w", suffix=".prompt.md",
                                         delete=False) as handle:
            handle.write(prompt[:60000])
            prompt_file = handle.name
        try:
            with open(prompt_file, encoding="utf-8") as stdin_handle:
                proc = subprocess.run(
                    [binary, "exec", "-s", "read-only",
                     "--skip-git-repo-check", "-"],
                    stdin=stdin_handle, capture_output=True, text=True,
                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise ProviderError("TIMEOUT", f"{timeout}s") from None
        finally:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass
        if proc.returncode != 0:
            raise ProviderError("PROVIDER_FAIL",
                                redact((proc.stderr or "")[-300:]))
        text = (proc.stdout or "").strip()
        if not text:
            raise ProviderError("INVALID_RESPONSE", "empty stdout")
        return Generation(text=text, provider=self.name,
                          model=self.model_name(),
                          latency_ms=int((time.monotonic() - started) * 1000))


def on_demand_chain(config, name: str) -> list:
    """Explicit external provider by name. Empty when unavailable."""
    from ..providers import build_providers
    providers = build_providers(config)
    providers["codex-cli"] = CodexCliProvider(config)
    provider = providers.get(name)
    if provider is None:
        return []
    try:
        ok, _ = provider.is_available()
    except Exception:
        ok = False
    return [provider] if ok else []
