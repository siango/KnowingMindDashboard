"""V2 configuration from environment. Fail-closed, no secret storage.

All paid API use requires KMW2_SPENDING_BUDGET_USD > 0 plus an explicit
price schedule. No model names or prices are assumed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "").strip() or default))
    except ValueError:
        return default


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


@dataclass(frozen=True)
class WorkerConfig:
    ollama_url: str = "http://127.0.0.1:11434"
    qwen_model: str = ""  # empty = auto-select from local inventory
    qwen_fallback_model: str = ""  # empty = no fallback; must exist in /api/tags when set
    deepseek_local_model: str = ""  # must be explicit; never assumed
    deepseek_local_min_mem_mb: int = 6144
    deepseek_api_enabled: bool = False
    deepseek_api_base: str = "https://api.deepseek.com/v1"
    deepseek_api_model: str = ""  # must be explicit; never assumed
    spending_budget_usd: float = 0.0
    deepseek_price_in_per_1k: float | None = None
    deepseek_price_out_per_1k: float | None = None
    allow_echo_fallback: bool = False
    max_repair_attempts: int = 2
    task_timeout_s: int = 600
    test_timeout_s: int = 300
    max_files_per_task: int = 10
    max_bytes_per_task: int = 200_000
    max_patch_bytes: int = 64_000
    owner_node: str = "AV15"
    # Key is referenced by name only; value is read at call time, never stored.
    api_key_env: str = field(default="KMW2_DEEPSEEK_API_KEY", repr=False)

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        price_in = os.environ.get("KMW2_DEEPSEEK_PRICE_PER_1K_IN", "").strip()
        price_out = os.environ.get("KMW2_DEEPSEEK_PRICE_PER_1K_OUT", "").strip()
        return cls(
            ollama_url=_env_str("KMW2_OLLAMA_URL", "http://127.0.0.1:11434"),
            qwen_model=_env_str("KMW2_QWEN_MODEL", ""),
            qwen_fallback_model=_env_str("KMW2_QWEN_FALLBACK_MODEL", ""),
            deepseek_local_model=_env_str("KMW2_DEEPSEEK_LOCAL_MODEL", ""),
            deepseek_local_min_mem_mb=_env_int("KMW2_DEEPSEEK_LOCAL_MIN_MEM_MB", 6144),
            deepseek_api_enabled=_env_flag("KMW2_DEEPSEEK_API_ENABLED"),
            deepseek_api_base=_env_str("KMW2_DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"),
            deepseek_api_model=_env_str("KMW2_DEEPSEEK_API_MODEL", ""),
            spending_budget_usd=_env_float("KMW2_SPENDING_BUDGET_USD", 0.0),
            deepseek_price_in_per_1k=float(price_in) if price_in else None,
            deepseek_price_out_per_1k=float(price_out) if price_out else None,
            allow_echo_fallback=_env_flag("KMW2_ALLOW_ECHO_FALLBACK"),
            max_repair_attempts=_env_int("KMW2_MAX_REPAIR_ATTEMPTS", 2),
            task_timeout_s=_env_int("KMW2_TASK_TIMEOUT_S", 600),
            test_timeout_s=_env_int("KMW2_TEST_TIMEOUT_S", 300),
            max_files_per_task=_env_int("KMW2_MAX_FILES_PER_TASK", 10),
            max_bytes_per_task=_env_int("KMW2_MAX_BYTES_PER_TASK", 200_000),
            max_patch_bytes=_env_int("KMW2_MAX_PATCH_BYTES", 64_000),
            owner_node=_env_str("KMW2_OWNER_NODE", "AV15"),
        )

    def paid_api_allowed(self) -> tuple[bool, str]:
        """Gate for any paid call. Returns (allowed, reason)."""
        if not self.deepseek_api_enabled:
            return False, "API_NOT_ENABLED"
        if self.spending_budget_usd <= 0:
            return False, "SPENDING_BUDGET_NOT_CONFIGURED"
        if not self.deepseek_api_model:
            return False, "API_MODEL_NOT_CONFIGURED"
        if self.deepseek_price_in_per_1k is None or self.deepseek_price_out_per_1k is None:
            return False, "PRICING_UNCONFIGURED"
        if not os.environ.get(self.api_key_env, "").strip():
            return False, "API_KEY_MISSING"
        return True, "ALLOWED"
