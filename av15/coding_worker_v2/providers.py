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
import math
import fcntl
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from email.utils import parsedate_to_datetime
from contextlib import contextmanager
from types import SimpleNamespace

from .config import WorkerConfig

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|bearer\s+[A-Za-z0-9._-]+)")
# Per-request keep-alive so multi-turn CLI sessions skip the ~26s cold load.
# Server MAX_LOADED_MODELS=1 still bounds RAM; switching models evicts.
OLLAMA_KEEP_ALIVE = "5m"


class ProviderError(Exception):
    def __init__(self, reason: str, detail: str = "", *, resources=None, trace=None):
        super().__init__(f"{reason}: {redact(detail)}" if detail else reason)
        self.reason = reason
        self.detail = redact(detail)
        self.resources = resources or {}
        self.trace = trace or []


def resource_headers(headers, now: float | None = None) -> dict:
    """Only standard disclosed limits; missing values are never inferred."""
    now = time.time() if now is None else now
    headers = headers or {}
    result = {"quota": "UNKNOWN", "remaining": None, "limit": None,
              "reset_at": None, "retry_at": None,
              "observed_at": now, "source": "HTTP response headers"}
    for key, header in (("remaining", "X-RateLimit-Remaining"),
                        ("limit", "X-RateLimit-Limit"),
                        ("reset_at", "X-RateLimit-Reset")):
        try:
            value = float(headers.get(header, ""))
            if math.isfinite(value) and value >= 0:
                result[key] = value
        except (ValueError, TypeError):
            continue
    retry = headers.get("Retry-After")
    if retry is not None:
        try:
            seconds = float(retry)
            if math.isfinite(seconds) and seconds >= 0:
                result["retry_at"] = now + seconds
        except (ValueError, TypeError):
            try:
                result["retry_at"] = max(now, parsedate_to_datetime(retry).timestamp())
            except (ValueError, TypeError, OverflowError):
                pass
    if result["remaining"] is not None:
        result["quota"] = ("EXHAUSTED" if result["remaining"] == 0 else
                           "LOW_QUOTA" if result["limit"] and
                           result["remaining"] / result["limit"] <= 0.1 else "AVAILABLE")
    return result


def redact(text: str) -> str:
    if not isinstance(text, str):
        return ""
    red = SECRET_KEY_RE.sub("[REDACTED]", text)
    key = os.environ.get("KMW2_DEEPSEEK_API_KEY", "").strip()
    if key and len(key) >= 8:
        red = red.replace(key, "[REDACTED]")
    red = re.sub(r"(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})", "[REDACTED]", red)
    for name, value in os.environ.items():
        if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY", name, re.I) and len(value) >= 8:
            red = red.replace(value, "[REDACTED]")
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
              timeout: int = 30, max_bytes: int = 1_000_000,
              resources: dict | None = None) -> dict:
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
            if resources is not None:
                resources.update(resource_headers(resp.headers))
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        observed = resource_headers(exc.headers)
        reason = "RATE_LIMITED" if exc.code == 429 else "HTTP_ERROR"
        try:
            body = json.loads(exc.read(8192))
            code = body.get("error", {}).get("code") if isinstance(body, dict) else None
            if code in {"insufficient_quota", "quota_exhausted"}:
                reason = "QUOTA_EXHAUSTED"
        except (ValueError, AttributeError, OSError):
            pass
        if reason == "QUOTA_EXHAUSTED":
            observed["quota"] = "EXHAUSTED"
        raise ProviderError(reason, f"status={exc.code}", resources=observed) from None
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
    execution: dict = field(default_factory=dict)


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
              timeout: int, structured: bool | dict = False) -> Generation:
        started = time.monotonic()
        resources = {}
        data = http_json(self.base_url() + "/api/chat", {
            "model": model, "stream": False, "think": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "messages": messages,
            **({"format": structured if isinstance(structured, dict) else "json"} if structured else {}),
            "options": {"num_predict": max_tokens},
        }, timeout=timeout, resources=resources)
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
            tokens_in=data.get("prompt_eval_count") if type(
                data.get("prompt_eval_count")) is int else None,
            tokens_out=data.get("eval_count") if type(
                data.get("eval_count")) is int else None,
            latency_ms=int((time.monotonic() - started) * 1000),
            execution={"resources": resources, "actual_cost_usd": None,
                       "usage_source": "OLLAMA_EVAL_COUNTS"})

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

    def generate_plan(self, messages: list[dict], max_tokens: int = 4096,
                      timeout: int = 120, response_schema: dict | None = None) -> Generation:
        errors = []
        for model, _ in self._candidates():
            try:
                return self._chat(model, messages, max_tokens, timeout, structured=response_schema or True)
            except ProviderError as exc:
                if exc.reason in {"RATE_LIMITED", "QUOTA_EXHAUSTED"}:
                    raise
                errors.append(f"{model}={exc.reason}")
        raise ProviderError("ALL_QWEN_MODELS_FAILED", "; ".join(errors))

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
                if exc.reason in {"RATE_LIMITED", "QUOTA_EXHAUSTED"}:
                    raise
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
        if not self.path.exists():
            return {"spent_usd": 0.0, "calls": 0, "reservations": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            spent = float(data["spent_usd"])
            calls = data["calls"]
            reservations = data.get("reservations", {})
            if (not math.isfinite(spent) or spent < 0 or type(calls) is not int
                    or calls < 0 or not isinstance(reservations, dict)
                    or any(type(v) not in (int, float) or not math.isfinite(v)
                           or v < 0 for v in reservations.values())):
                raise ValueError("invalid ledger")
            return {"spent_usd": spent, "calls": calls, "reservations": reservations}
        except (OSError, ValueError, TypeError, KeyError):
            raise ProviderError("BUDGET_LEDGER_UNREADABLE") from None

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def _write(self, state):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self.path)

    def reserve(self, amount: float, budget: float) -> str:
        if (not math.isfinite(amount) or amount < 0
                or not math.isfinite(budget) or budget < 0):
            raise ProviderError("BUDGET_INVALID")
        with self.locked():
            state = self.read()
            if state["spent_usd"] + sum(state["reservations"].values()) + amount > budget:
                raise ProviderError("BUDGET_EXCEEDED")
            reference = uuid.uuid4().hex
            state["reservations"][reference] = amount
            self._write(state)
            return reference

    def settle(self, reference: str, cost: float | None) -> None:
        with self.locked():
            state = self.read()
            if reference not in state["reservations"]:
                raise ProviderError("BUDGET_RESERVATION_MISSING")
            if cost is not None:
                del state["reservations"][reference]
                state["spent_usd"] = round(state["spent_usd"] + cost, 6)
            state["calls"] += 1
            self._write(state)

    def add(self, cost_usd: float) -> dict:
        with self.locked():
            state = self.read()
            state["spent_usd"] = round(state["spent_usd"] + cost_usd, 6)
            state["calls"] += 1
            self._write(state)
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
        state = self.ledger.read()
        spent = state["spent_usd"] + sum(state["reservations"].values())
        if spent >= self.config.spending_budget_usd:
            return False, "BUDGET_EXHAUSTED"
        return True, f"MODEL={self.config.deepseek_api_model}"

    def capabilities(self) -> Capabilities:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("BUDGET_EXCEEDED" if "BUDGET" in reason
                                else "UNAVAILABLE", reason)
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
        input_bound = max(8000, len(json.dumps(messages).encode("utf-8")))
        worst = self._cost(input_bound, max_tokens)
        reservation = self.ledger.reserve(worst, self.config.spending_budget_usd)
        started = time.monotonic()
        resources = {}
        data = http_json(self.config.deepseek_api_base.rstrip("/") + "/chat/completions",
                         {"model": self.config.deepseek_api_model,
                          "messages": messages, "max_tokens": max_tokens,
                          "stream": False},
                         headers={"Authorization": "Bearer " + key},
                         timeout=timeout, resources=resources)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("INVALID_RESPONSE", "choices shape") from None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("INVALID_RESPONSE", "empty content")
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        tin = usage.get("prompt_tokens")
        tout = usage.get("completion_tokens")
        tin = tin if type(tin) is int and tin >= 0 else None
        tout = tout if type(tout) is int and tout >= 0 else None
        cost = self._cost(tin, tout) if tin is not None and tout is not None else None
        self.ledger.settle(reservation, cost)
        return Generation(text=text, provider=self.name,
                          model=self.config.deepseek_api_model,
                          tokens_in=tin, tokens_out=tout,
                          latency_ms=int((time.monotonic() - started) * 1000),
                          execution={"resources": resources, "actual_cost_usd": None,
                                     "estimated_cost_usd": cost,
                                     "cost_basis": "measured tokens and configured prices; not billed cost",
                                     "usage_source": "OPENAI_COMPATIBLE_USAGE_FIELDS",
                                     # Reservations are budget holds, never billed cost.
                                     "reservation_ref": reservation,
                                     "budget_reserved_usd": worst if cost is None else 0})


class MuseCliProvider(BaseProvider):
    """Existing Muse headless contract (variant A baseline). No API keys."""

    name = "muse-cli"
    local = False

    def binary(self) -> str | None:
        return shutil.which("muse")

    def is_available(self) -> tuple[bool, str]:
        if os.environ.get("KMW2_MUSE_ENABLED") != "1":
            return False, "MUSE_NOT_CONFIGURED"
        if not self.binary():
            return False, "MUSE_BINARY_ABSENT"
        return False, "MUSE_READ_ONLY_CONTRACT_UNVERIFIED"

    def capabilities(self) -> Capabilities:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
        return Capabilities(provider=self.name, model="muse-exec", local=False)

    def generate(self, messages: list[dict], max_tokens: int = 1024,
                 timeout: int = 120) -> Generation:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderError("UNAVAILABLE", reason)
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
                          latency_ms=1,
                          execution={"usage_source": "WORD_COUNT_NOT_TOKENS"})


@dataclass(frozen=True)
class RouteAttempt:
    provider: str
    model: str
    ok: bool
    reason: str = ""


class ProviderResources:
    """Cooldown and measured history inside the existing queue checkpoints."""

    KEY = ".provider-resources"

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def read(self) -> dict:
        checkpoint = self.store.load_checkpoint(self.KEY)
        if checkpoint is None:
            if (self.store.checkpoints / f"{self.KEY}.json").exists():
                raise ProviderError("RESOURCE_STATE_UNREADABLE")
            return {}
        data = checkpoint.get("data")
        if not isinstance(data, dict):
            raise ProviderError("RESOURCE_STATE_UNREADABLE")
        return data

    def update(self, name, change):
        with self.store._locked():
            state = self.read()
            row = dict(state.get(name, {}))
            change(row)
            state[name] = row
            self.store.save_checkpoint(self.KEY, "provider_resources", state)
            return row

    def gate(self, name) -> tuple[bool, str, float | None]:
        row = self.read().get(name, {})
        until = row.get("blocked_until")
        if until is not None and self.clock() < until:
            return False, row.get("status", "RATE_LIMITED"), until
        if row.get("status") == "EXHAUSTED" and until is None:
            return False, "EXHAUSTED", None
        return True, "READY", None

    def ordered(self, chain):
        from .agent.memory import strategy_effectiveness
        state = self.read()
        outcomes = strategy_effectiveness(self.store, self.store.base.parent / "receipts")
        def rank(provider):
            row = state.get(provider.name, {})
            tasks = outcomes.get(provider.name, {})
            terminal = tasks.get("verified", 0) + tasks.get("failed", 0)
            return (provider.name != "qwen-local",
                    not getattr(provider, "local", False),
                    tasks.get("failed", 0) / terminal if terminal else 0.5,
                    row.get("mean_latency_ms", float("inf")),
                    row.get("mean_cost_usd", float("inf")))
        return sorted(chain, key=rank)

    def failed(self, name, error):
        def change(row):
            failures = row.get("consecutive_failures", 0) + 1
            observed = error.resources
            status = ("EXHAUSTED" if error.reason == "QUOTA_EXHAUSTED" or
                      "BUDGET" in error.reason else
                      "RATE_LIMITED" if error.reason == "RATE_LIMITED" else "UNAVAILABLE")
            deadlines = [v for v in (observed.get("retry_at"), observed.get("reset_at"))
                         if isinstance(v, (int, float)) and v > self.clock()]
            until = (max(deadlines) if deadlines else None if status == "EXHAUSTED"
                     else self.clock() + min(900, 30 * 2 ** min(failures - 1, 5)))
            row.update(status=status, quota=observed.get("quota", "UNKNOWN"),
                       remaining=observed.get("remaining"), limit=observed.get("limit"),
                       reset_at=observed.get("reset_at"), blocked_until=until,
                       cooldown_source="provider" if deadlines else "local backoff policy",
                       observed_at=self.clock(), source=observed.get("source", "execution error"),
                       last_reason=error.reason, consecutive_failures=failures,
                       failures=row.get("failures", 0) + 1)
        return self.update(name, change)

    def succeeded(self, generation):
        def change(row):
            count = row.get("generations_succeeded", 0) + 1
            cost = generation.execution.get("actual_cost_usd")
            if cost is None:
                cost = generation.execution.get("estimated_cost_usd")
            observed = generation.execution.get("resources", {})
            row.update(status=observed.get("quota", "AVAILABLE")
                       if observed.get("quota") != "UNKNOWN" else "AVAILABLE",
                       quota=observed.get("quota", "UNKNOWN"),
                       remaining=observed.get("remaining"), limit=observed.get("limit"),
                       reset_at=observed.get("reset_at"),
                       blocked_until=observed.get("reset_at")
                       if observed.get("quota") == "EXHAUSTED"
                       and (observed.get("reset_at") or 0) > self.clock() else None,
                       consecutive_failures=0, generations_succeeded=count,
                       model=generation.model, observed_at=self.clock(),
                       source=observed.get("source", "successful inference; quota not disclosed"),
                       mean_latency_ms=(row.get("mean_latency_ms", 0) * (count - 1)
                                        + generation.latency_ms) / count,
                       last_tokens_in=generation.tokens_in,
                       last_tokens_out=generation.tokens_out,
                       actual_cost_usd=generation.execution.get("actual_cost_usd"),
                       estimated_cost_usd=generation.execution.get("estimated_cost_usd"),
                       last_reason="SUCCESS")
            if cost is not None:
                costs = row.get("cost_observations", 0)
                row["mean_cost_usd"] = (row.get("mean_cost_usd", 0) * costs + cost) / (costs + 1)
                row["cost_observations"] = costs + 1
        return self.update(generation.provider, change)

    def invoke(self, provider, task, messages, max_tokens, timeout, *, generate_fn=None):
        if not re.fullmatch(r"[a-z0-9-]+", provider.name):
            raise ProviderError("PROVIDER_NAME_INVALID")
        with (self.store.checkpoints / f".provider-{provider.name}.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            allowed, reason, until = self.gate(provider.name)
            if not allowed:
                raise ProviderError("PROVIDER_COOLDOWN", reason,
                                    resources={"retry_at": until})
            external = not getattr(provider, "local", False)
            if external and provider.name != "deepseek-api":
                hints = task.task_input.hints
                if (hints.get("external_authorized") is not True
                        or hints.get("billing_mode") != "existing-subscription"):
                    raise ProviderError("EXTERNAL_NOT_AUTHORIZED")
                prior = (self.store.load_checkpoint(task.task_id) or {}).get("data", {})
                limit = hints.get("max_provider_dispatches", 0)
                used = prior.get("external_dispatches", 0)
                if prior.get("dispatch_state") in {"pending", "ambiguous"}:
                    raise ProviderError("DISPATCH_RECONCILIATION_REQUIRED")
                if type(limit) is not int or used >= limit:
                    raise ProviderError("EXTERNAL_DISPATCH_BUDGET_EXHAUSTED")
                self.store.checkpoint_task(task, "dispatching", {
                    "external_dispatches": used + 1, "dispatch_state": "pending",
                    "dispatch_provider": provider.name})
            try:
                generation = (generate_fn or provider.generate)(messages, max_tokens, timeout)
            except ProviderError as error:
                self.failed(provider.name, error)
                if external and provider.name != "deepseek-api":
                    self.store.checkpoint_task(task, "dispatch_failed", {
                        "dispatch_state": "ambiguous" if error.reason in
                        {"TIMEOUT", "CLI_EXEC_FAILED"} else "returned_error"})
                raise
            if external and provider.name != "deepseek-api":
                self.store.checkpoint_task(task, "dispatch_returned", {
                    "dispatch_state": "returned"})
            self.succeeded(generation)
            return generation


def execution_chain(config, task, ledger_path=None):
    """Only configured proposal providers; external inference needs task authority."""
    from .agent.providers_ext import CodexCliProvider, CopilotCliProvider
    hints = task.task_input.hints
    authorized = (hints.get("external_authorized") is True
                  and hints.get("billing_mode") == "existing-subscription")
    providers = {"qwen-local": QwenProvider(config)}
    if config.deepseek_local_model:
        providers["deepseek-local"] = DeepSeekLocalProvider(config)
    if config.deepseek_api_enabled:
        providers["deepseek-api"] = DeepSeekAPIProvider(config, ledger_path)
    for name, cls, enabled in (
            ("codex-cli", CodexCliProvider, "KMW2_CODEX_ENABLED"),
            ("copilot-cli", CopilotCliProvider, "KMW2_COPILOT_ENABLED")):
        if os.environ.get(enabled) == "1" or (authorized and
                task.task_input.preferred_provider in ("auto", name)):
            providers[name] = cls(config, authorized=authorized,
                                  workspace=task.task_input.repo)
    if os.environ.get("KMW2_MUSE_ENABLED") == "1" and authorized:
        providers["muse-cli"] = MuseCliProvider(config)
    preferred = task.task_input.preferred_provider
    if preferred and preferred != "auto":
        return [providers[preferred]] if preferred in providers else []
    return list(providers.values())


def provider_inventory(config, store, workspace):
    """Read-only probes, no inference. Quota freshness is independent of availability."""
    task = SimpleNamespace(task_input=SimpleNamespace(
        hints={}, preferred_provider="auto", repo=workspace))
    resources = ProviderResources(store)
    state = resources.read()
    rows = []
    for provider in execution_chain(
            config, task, store.base.parent / "state" / "spend_ledger.json"):
        allowed, reason, until = resources.gate(provider.name)
        row = dict(state.get(provider.name, {}))
        quota_fresh = ((time.time() - row.get("observed_at", 0)) <= 300
                       and (row.get("reset_at") is None
                            or time.time() < row["reset_at"]))
        probe = {}
        if allowed:
            if hasattr(provider, "probe"):
                probe = provider.probe()
                available, reason = probe["available"], probe["reason"]
            else:
                available, reason = provider.is_available()
            status = (row.get("quota") if quota_fresh and
                      row.get("quota") in {"LOW_QUOTA", "EXHAUSTED"} else "AVAILABLE"
                      ) if available else "UNAVAILABLE"
        else:
            available, status = False, row.get("status", "UNKNOWN")
        rows.append({**row, **probe, "provider": provider.name, "available": available,
                     "status": status, "reason": reason, "retry_at": until,
                     "quota": row.get("quota", "UNKNOWN") if quota_fresh else "UNKNOWN",
                     "quota_fresh": quota_fresh, "remaining": row.get("remaining")
                     if quota_fresh else None, "probe_observed_at": time.time(),
                     "probe_source": "configured provider read-only availability/authentication probe"})
    return rows


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
                           timeout: int = 120, *, resources=None, task=None,
                           progress=None) -> tuple[Generation, list[RouteAttempt]]:
    """Try each provider in order. Explicit per-provider failure records."""
    trace: list[RouteAttempt] = []
    if not chain:
        raise ProviderError("NO_PROVIDER_AVAILABLE", "empty chain")
    for provider in resources.ordered(chain) if resources else chain:
        if resources:
            allowed, reason, _ = resources.gate(provider.name)
            if not allowed:
                trace.append(RouteAttempt(provider.name, "", False, reason))
                continue
        try:
            caps = provider.capabilities()
            model = caps.model
        except ProviderError as exc:
            if resources:
                resources.failed(provider.name, exc)
            trace.append(RouteAttempt(provider.name, "", False, exc.reason))
            continue
        try:
            if progress:
                progress(phase="PROVIDER_EXECUTE", state="WAITING",
                         provider=provider.name, model=model, timeout=timeout,
                         waiting_for=provider.name, reason="CAPABILITY_BUDGET_QUOTA_HISTORY")
            gen = (resources.invoke(provider, task, messages, max_tokens, timeout)
                   if resources else provider.generate(messages, max_tokens, timeout))
        except ProviderError as exc:
            trace.append(RouteAttempt(provider.name, model, False, exc.reason))
            continue
        trace.append(RouteAttempt(provider.name, model, True, "OK"))
        return gen, trace
    reasons = "; ".join(f"{t.provider}={t.reason}" for t in trace)
    raise ProviderError("ALL_PROVIDERS_FAILED", reasons, trace=trace)
