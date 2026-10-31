"""Phase 1: provider abstraction. Local-first, fail-closed, no secret leaks.

Providers:
  qwen-local        Qwen Coder via local Ollama (loopback only)
  deepseek-local    DeepSeek via local Ollama, hardware-gated (loopback only)
  deepseek-api      DeepSeek API, budget+price gated (disabled by default)
  muse-cli          Existing Muse headless contract (baseline / variant A)
  echo              Deterministic offline fallback for tests only

Every failure is explicit (ProviderError.reason). API keys never appear in
logs, receipts, or exceptions.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .config import WorkerConfig

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|bearer\s+[A-Za-z0-9._-]+)")
# Per-request keep-alive so multi-turn CLI sessions skip the ~26s cold load.
# Server MAX_LOADED_MODELS=1 still bounds RAM; switching models evicts.
OLLAMA_KEEP_ALIVE = "5m"


class ProviderError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {redact(detail)}" if detail else reason)
        self.reason = reason
        self.detail = redact(detail)


def redact(text: str) -> str:
    if not isinstance(text, str):
        return ""
    red = SECRET_KEY_RE.sub("[REDACTED]", text)
    key = os.environ.get("KMW2_DEEPSEEK_API_KEY", "").strip()
    if key and len(key) >= 8:
        red = red.replace(key, "[REDACTED]")
    return red[:2000]


def require_loopback(url: str) -> str:
    """Local providers may only talk to loopback. Returns normalized base."""
    base = (url or "").strip().rstrip("/")
    try:
        parts = urlsplit(base)
    except ValueError as exc:
        raise ProviderError("POLICY_DENY", f"bad url: {exc}")
    if parts.scheme != "http" or parts.hostname not in LOOPBACK_HOSTS:
        raise ProviderError("POLICY_DENY", "local provider requires http loopback")
    if parts.username or parts.password:
        raise ProviderError("POLICY_DENY", "credentials in url forbidden")
    return base


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url: str, payload: dict | None = None, headers: dict | None = None,
              timeout: int = 30, max_bytes: int = 1_000_000) -> dict:
    data = None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers,
                                 method="POST" if data else "GET")
    try:
        # No proxy: local providers are loopback-only; paid API goes direct
        # so a sandbox proxy can neither observe keys nor fake responses.
        with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderError("HTTP_ERROR", f"status={exc.code}") from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise ProviderError("UNAVAILABLE", type(exc).__name__) from None
    if len(raw) > max_bytes:
        raise ProviderError("INVALID_RESPONSE", "response too large")
    try:
        value = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ProviderError("INVALID_RESPONSE", "not json") from None
    if not isinstance(value, dict):
        raise ProviderError("INVALID_RESPONSE", "top level not object")
    return value


@dataclass(frozen=True)
class Capabilities:
    provider: str
    model: str
    local: bool
    version: str = ""  # digest / modified_at from inventory when known
    chat: bool = True


@dataclass(frozen=True)
class Generation:
    text: str
    provider: str
    model: str
    version: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int = 0


class BaseProvider:
    name = "base"
    local = True

    def __init__(self, config: WorkerConfig):
        self.config = config

    def is_available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def capabilities(self) -> Capabilities:
        raise NotImplementedError

    def generate(self, messages: list[dict], max_tokens: int = 1024,
                 timeout: int = 120) -> Generation:
        raise NotImplementedError


class OllamaProvider(BaseProvider):
    """Shared Ollama loopback transport + inventory discovery."""

    name = "ollama"
    # Subclasses set preference substrings, first match wins.
    preferences: tuple[str, ...] = ()

    def base_url(self) -> str:
        return require_loopback(self.config.ollama_url)

    def inventory(self) -> list[dict]:
        data = http_json(self.base_url() + "/api/tags", timeout=10)
        models = data.get("models", [])
        if not isinstance(models, list):
            raise ProviderError("INVALID_RESPONSE", "tags models not list")
        out = []
        for item in models:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                out.append({"name": item["name"],
                            "digest": str(item.get("digest", ""))[:64],
                            "modified_at": str(item.get("modified_at", ""))[:32]})
        return out

    def select_model(self, explicit: str = "") -> tuple[str, str]:
        """Returns (model, version). Explicit name must exist in inventory."""
        models = self.inventory()
        names = {m["name"]: m for m in models}
        if explicit:
            if explicit not in names:
                raise ProviderError("MODEL_NOT_IN_INVENTORY", explicit)
            meta = names[explicit]
            return explicit, meta["digest"] or meta["modified_at"]
        lowered = {n.lower(): n for n in names}
        for pref in self.preferences:
            for low, orig in sorted(lowered.items()):
                if pref in low:
                    meta = names[orig]
                    return orig, meta["digest"] or meta["modified_at"]
        if len(names) == 1:
            only = next(iter(names.values()))
            return only["name"], only["digest"] or only["modified_at"]
        raise ProviderError("MODEL_SELECTION_REQUIRED",
                            f"{len(names)} models, no match")

    def _chat(self, model: str, messages: list[dict], max_tokens: int,
              timeout: int) -> Generation:
        started = time.monotonic()
        data = http_json(self.base_url() + "/api/chat", {
            "model": model, "stream": False, "think": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "messages": messages,
            "options": {"num_predict": max_tokens},
        }, timeout=timeout)
        try:
            text = data.get("message", {}).get("content", "")
        except AttributeError:
            raise ProviderError("INVALID_RESPONSE", "message shape") from None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("INVALID_RESPONSE", "empty content")
        digest = ""
        try:
            digest = self._version_for(model)
        except ProviderError:
            digest = ""
        return Generation(
            text=text, provider=self.name, model=model, version=digest,
            tokens_in=data.get("prompt_eval_count") if isinstance(
                data.get("prompt_eval_count"), int) else None,
            tokens_out=data.get("eval_count") if isinstance(
                data.get("eval_count"), int) else None,
            latency_ms=int((time.monotonic() - started) * 1000))

    def _version_for(self, model: str) -> str:
        for item in self.inventory():
            if item["name"] == model:
                return item["digest"] or item["modified_at"]
        return ""


class QwenProvider(OllamaProvider):
    name = "qwen-local"
    preferences = ("qwen2.5-coder", "qwen3", "qwen", "coder")

    def _candidates(self) -> list[tuple[str, str]]:
        """Primary then fallback model, resolved against live inventory.

        Empty primary auto-selects. A configured fallback that is missing
        from inventory is skipped; when nothing resolves the last
        inventory error is raised unchanged.
        """
        slots = [self.config.qwen_model]
        fallback = self.config.qwen_fallback_model
        if fallback and fallback not in slots:
            slots.append(fallback)
        if len(slots) == 1 and not slots[0]:
            return [self.select_model("")]
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        last_err: ProviderError | None = None
        for slot in slots:
            try:
                model, version = self.select_model(slot)
            except ProviderError as exc:
                last_err = exc
                continue
            if model not in seen:
                seen.add(model)
                out.append((model, version))
        if not out and last_err is not None:
            raise last_err
        return out

    def is_available(self) -> tuple[bool, str]:
        try:
            models = self._candidates()
        except ProviderError as exc:
            return False, exc.reason
        if not models:
            return False, "MODEL_NOT_IN_INVENTORY"
        extra = f"+FALLBACK={models[1][0]}" if len(models) > 1 else ""
        return True, f"MODEL={models[0][0]}{extra}"

    def capabilities(self) -> Capabilities:
        models = self._candidates()
        if not models:
            raise ProviderError("MODEL_NOT_IN_INVENTORY", self.config.qwen_model)
        return Capabilities(provider=self.name, model=models[0][0], local=True,
                            version=models[0][1])

    def generate(self, messages: list[dict], max_tokens: int = 1024,
                 timeout: int = 120) -> Generation:
        models = self._candidates()
        if not models:
            raise ProviderError("MODEL_NOT_IN_INVENTORY", self.config.qwen_model)
        errors: list[str] = []
        for model, _ in models:
            try:
                return self._chat(model, messages, max_tokens, timeout)
            except ProviderError as exc:
                errors.append(f"{model}={exc.reason}")
        raise ProviderError("ALL_QWEN_MODELS_FAILED", "; ".join(errors))


def mem_available_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class DeepSeekLocalProvider(OllamaProvider):
    name = "deepseek-local"
    preferences = ("deepseek",)

    def is_available(self) -> tuple[bool, str]:
        if not self.config.deepseek_local_model:
            return False, "MODEL_NOT_CONFIGURED"
        avail = mem_available_mb()
        if avail is not None and avail < self.config.deepseek_local_min_mem_mb:
            return False, f"INSUFFICIENT_MEMORY avail={avail} need={self.config.deepseek_local_min_mem_mb}"
        try:
            model, _ = self.select_model(self.config.deepseek_local_model)
        except ProviderError as exc:
            return False, exc.reason
        return True, f"MODEL={model}"

    def capabilities(self) -> Capabilities:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        _, version = self.select_model(self.config.deepseek_local_model)
        return Capabilities(provider=self.name,
                            model=self.config.deepseek_local_model,
                            local=True, version=version)

    def generate(self, messages: list[dict], max_tokens: int = 1024,
                 timeout: int = 120) -> Generation:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        return self._chat(self.config.deepseek_local_model, messages,
                          max_tokens, timeout)


class SpendLedger:
    """Durable local spend tracking. Paid calls refuse past budget."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {"spent_usd": float(data.get("spent_usd", 0.0)),
                        "calls": int(data.get("calls", 0))}
        except (OSError, ValueError, TypeError):
            pass
        return {"spent_usd": 0.0, "calls": 0}

    def add(self, cost_usd: float) -> dict:
        state = self.read()
        state["spent_usd"] = round(state["spent_usd"] + cost_usd, 6)
        state["calls"] += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self.path)
        return state


class DeepSeekAPIProvider(BaseProvider):
    name = "deepseek-api"
    local = False

    def __init__(self, config: WorkerConfig, ledger_path: Path | None = None):
        super().__init__(config)
        default = Path(__file__).resolve().parent / "state" / "spend_ledger.json"
        self.ledger = SpendLedger(ledger_path or default)

    def is_available(self) -> tuple[bool, str]:
        allowed, reason = self.config.paid_api_allowed()
        if not allowed:
            return False, reason
        spent = self.ledger.read()["spent_usd"]
        if spent >= self.config.spending_budget_usd:
            return False, "BUDGET_EXHAUSTED"
        return True, f"MODEL={self.config.deepseek_api_model}"

    def capabilities(self) -> Capabilities:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        return Capabilities(provider=self.name,
                            model=self.config.deepseek_api_model, local=False)

    def _cost(self, tokens_in: int, tokens_out: int) -> float:
        assert self.config.deepseek_price_in_per_1k is not None
        assert self.config.deepseek_price_out_per_1k is not None
        return round(tokens_in / 1000 * self.config.deepseek_price_in_per_1k
                     + tokens_out / 1000 * self.config.deepseek_price_out_per_1k, 6)

    def generate(self, messages: list[dict], max_tokens: int = 1024,
                 timeout: int = 120) -> Generation:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("BUDGET_EXCEEDED" if "BUDGET" in reason
                                else "UNAVAILABLE", reason)
        key = os.environ.get(self.config.api_key_env, "").strip()
        if not key:
            raise ProviderError("UNAVAILABLE", "API_KEY_MISSING")
        # Pre-check worst case against remaining budget.
        remaining = self.config.spending_budget_usd - self.ledger.read()["spent_usd"]
        worst = self._cost(8000, max_tokens)
        if worst > remaining:
            raise ProviderError("BUDGET_EXCEEDED",
                                f"worst_case={worst} remaining={round(remaining, 4)}")
        started = time.monotonic()
        data = http_json(self.config.deepseek_api_base.rstrip("/") + "/chat/completions",
                         {"model": self.config.deepseek_api_model,
                          "messages": messages, "max_tokens": max_tokens,
                          "stream": False},
                         headers={"Authorization": "Bearer " + key},
                         timeout=timeout)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("INVALID_RESPONSE", "choices shape") from None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("INVALID_RESPONSE", "empty content")
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        tin = usage.get("prompt_tokens", 0) if isinstance(
            usage.get("prompt_tokens"), int) else 0
        tout = usage.get("completion_tokens", 0) if isinstance(
            usage.get("completion_tokens"), int) else 0
        state = self.ledger.add(self._cost(tin, tout))
        _ = state
        return Generation(text=text, provider=self.name,
                          model=self.config.deepseek_api_model,
                          tokens_in=tin, tokens_out=tout,
                          latency_ms=int((time.monotonic() - started) * 1000))


class MuseCliProvider(BaseProvider):
    """Existing Muse headless contract (variant A baseline). No API keys."""

    name = "muse-cli"
    local = False

    def binary(self) -> str | None:
        return shutil.which("muse")

    def is_available(self) -> tuple[bool, str]:
        if not self.binary():
            return False, "MUSE_BINARY_ABSENT"
        return True, "HEADLESS_CONTRACT_ASSUMED"

    def capabilities(self) -> Capabilities:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        return Capabilities(provider=self.name, model="muse-exec", local=False)

    def generate(self, messages: list[dict], max_tokens: int = 1024,
                 timeout: int = 120) -> Generation:
        binary = self.binary()
        if not binary:
            raise ProviderError("UNAVAILABLE", "MUSE_BINARY_ABSENT")
        prompt = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}"
                             for m in messages if isinstance(m, dict))
        started = time.monotonic()
        with tempfile.NamedTemporaryFile("w", suffix=".prompt.md",
                                         delete=False) as handle:
            handle.write(prompt[:60000])
            prompt_file = handle.name
        try:
            proc = subprocess.run(
                [binary, "exec", "--provider",
                 os.environ.get("AV15_MUSE_PROVIDER", "meta"),
                 "--prompt-file", prompt_file],
                capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise ProviderError("TIMEOUT", f"{timeout}s") from None
        finally:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass
        if proc.returncode != 0:
            raise ProviderError("PROVIDER_FAIL", f"exit={proc.returncode}")
        text = (proc.stdout or "").strip()
        if not text:
            raise ProviderError("INVALID_RESPONSE", "empty stdout")
        return Generation(text=text, provider=self.name, model="muse-exec",
                          latency_ms=int((time.monotonic() - started) * 1000))


class EchoProvider(BaseProvider):
    """Deterministic offline provider. Tests and dry-runs only."""

    name = "echo"
    local = True

    def is_available(self) -> tuple[bool, str]:
        if self.config.allow_echo_fallback:
            return True, "ECHO_ENABLED"
        return False, "ECHO_NOT_ALLOWED"

    def capabilities(self) -> Capabilities:
        return Capabilities(provider=self.name, model="echo-1", local=True,
                            version="deterministic")

    def generate(self, messages: list[dict], max_tokens: int = 1024,
                 timeout: int = 120) -> Generation:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        last = ""
        for item in reversed(messages):
            if isinstance(item, dict) and item.get("role") == "user":
                last = str(item.get("content", ""))
                break
        return Generation(text=json.dumps({"echo": last[:4000]}),
                          provider=self.name, model="echo-1",
                          version="deterministic",
                          tokens_in=len(last.split()), tokens_out=8,
                          latency_ms=1)


@dataclass(frozen=True)
class RouteAttempt:
    provider: str
    model: str
    ok: bool
    reason: str = ""


def build_providers(config: WorkerConfig,
                    ledger_path: Path | None = None) -> dict[str, BaseProvider]:
    return {
        "qwen-local": QwenProvider(config),
        "deepseek-local": DeepSeekLocalProvider(config),
        "deepseek-api": DeepSeekAPIProvider(config, ledger_path),
        "muse-cli": MuseCliProvider(config),
        "echo": EchoProvider(config),
    }


def discover(config: WorkerConfig,
             ledger_path: Path | None = None) -> list[dict]:
    """Model capability discovery across all providers. Never raises."""
    out = []
    for name, provider in build_providers(config, ledger_path).items():
        try:
            ok, reason = provider.is_available()
        except Exception as exc:  # discovery must not crash routing
            ok, reason = False, f"PROBE_CRASH_{type(exc).__name__}"
        entry: dict = {"provider": name, "available": ok, "reason": redact(reason)}
        if ok:
            try:
                caps = provider.capabilities()
                entry["model"] = caps.model
                entry["local"] = caps.local
                entry["version"] = caps.version
            except ProviderError as exc:
                entry["available"] = False
                entry["reason"] = exc.reason
        out.append(entry)
    return out


def route_chain(config: WorkerConfig, preferred: str = "",
                ledger_path: Path | None = None) -> list[BaseProvider]:
    """Per-task chain: preferred first, then local-first fallback order."""
    providers = build_providers(config, ledger_path)
    order = ["qwen-local", "deepseek-local", "muse-cli", "deepseek-api", "echo"]
    if preferred and preferred in providers:
        order = [preferred] + [p for p in order if p != preferred]
    chain = []
    for name in order:
        provider = providers[name]
        try:
            ok, _ = provider.is_available()
        except Exception:
            ok = False
        if ok:
            chain.append(provider)
    return chain


def generate_with_fallback(messages: list[dict], chain: list[BaseProvider],
                           max_tokens: int = 1024,
                           timeout: int = 120) -> tuple[Generation, list[RouteAttempt]]:
    """Try each provider in order. Explicit per-provider failure records."""
    trace: list[RouteAttempt] = []
    if not chain:
        raise ProviderError("NO_PROVIDER_AVAILABLE", "empty chain")
    for provider in chain:
        try:
            caps = provider.capabilities()
            model = caps.model
        except ProviderError as exc:
            trace.append(RouteAttempt(provider.name, "", False, exc.reason))
            continue
        try:
            gen = provider.generate(messages, max_tokens, timeout)
        except ProviderError as exc:
            trace.append(RouteAttempt(provider.name, model, False, exc.reason))
            continue
        except Exception as exc:
            trace.append(RouteAttempt(provider.name, model, False,
                                      f"CRASH_{type(exc).__name__}"))
            continue
        trace.append(RouteAttempt(provider.name, model, True, "OK"))
        return gen, trace
    reasons = "; ".join(f"{t.provider}={t.reason}" for t in trace)
    raise ProviderError("ALL_PROVIDERS_FAILED", reasons)
