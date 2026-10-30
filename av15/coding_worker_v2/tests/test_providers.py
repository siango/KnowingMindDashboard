"""Phase 1 tests: providers, routing, fallback, budget guard, redaction."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.providers import (DeepSeekAPIProvider, ProviderError,  # noqa: E402
                                        QwenProvider, discover, generate_with_fallback,
                                        redact, require_loopback, route_chain)
from coding_worker_v2.tests import FakeBackend, FakeState  # noqa: E402


def env_config(**over):
    volatile = ("KMW2_QWEN_MODEL", "KMW2_QWEN_FALLBACK_MODEL", "KMW2_DEEPSEEK_LOCAL_MODEL",
                "KMW2_DEEPSEEK_API_MODEL",
                "KMW2_DEEPSEEK_PRICE_PER_1K_IN", "KMW2_DEEPSEEK_PRICE_PER_1K_OUT",
                "KMW2_DEEPSEEK_API_KEY", "KMW2_DEEPSEEK_API_BASE")
    for key in volatile:
        os.environ.pop(key, None)
    base = {"KMW2_OLLAMA_URL": over.pop("ollama", "http://127.0.0.1:9"),
            "KMW2_ALLOW_ECHO_FALLBACK": "0", "KMW2_DEEPSEEK_API_ENABLED": "0",
            "KMW2_SPENDING_BUDGET_USD": "0"}
    base.update(over)
    patcher = mock.patch.dict(os.environ, base, clear=False)
    patcher.start()
    self_ref = getattr(env_config, "_patchers", [])
    self_ref.append(patcher)
    env_config._patchers = self_ref
    return WorkerConfig.from_env()


class TestLoopback(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        for patcher in getattr(env_config, "_patchers", []):
            try:
                patcher.stop()
            except RuntimeError:
                pass
        env_config._patchers = []

    def test_non_loopback_rejected(self):
        with self.assertRaises(ProviderError):
            require_loopback("https://api.example.com")
        with self.assertRaises(ProviderError):
            require_loopback("http://10.0.0.5:11434")

    def test_loopback_accepted(self):
        self.assertTrue(require_loopback("http://127.0.0.1:11434").startswith("http"))

    def test_redaction(self):
        with mock.patch.dict(os.environ, {"KMW2_DEEPSEEK_API_KEY": "sk-secretvalue123"}):
            self.assertNotIn("sk-secretvalue123", redact("key=sk-secretvalue123"))
            self.assertIn("[REDACTED]", redact("api_key=abc"))


class TestQwen(FakeBackend):
    def test_inventory_and_select(self):
        config = env_config(ollama=self.base_url())
        provider = QwenProvider(config)
        ok, reason = provider.is_available()
        self.assertTrue(ok, reason)
        caps = provider.capabilities()
        self.assertEqual(caps.model, "qwen2.5-coder:0.5b")
        self.assertIn("sha256:fake123", caps.version)

    def test_generate(self):
        config = env_config(ollama=self.base_url())
        gen = QwenProvider(config).generate([{"role": "user", "content": "hi"}])
        self.assertEqual(gen.tokens_in, 10)
        self.assertEqual(gen.tokens_out, 5)
        self.assertTrue(gen.text)

    def test_explicit_missing_model(self):
        config = env_config(ollama=self.base_url(), KMW2_QWEN_MODEL="nope:99b")
        ok, reason = QwenProvider(config).is_available()
        self.assertFalse(ok)
        self.assertIn("MODEL_NOT_IN_INVENTORY", reason)

    def test_unreachable_explicit(self):
        config = env_config(ollama="http://127.0.0.1:9")
        ok, reason = QwenProvider(config).is_available()
        self.assertFalse(ok)
        self.assertEqual(reason, "UNAVAILABLE")


class TestRouting(FakeBackend):
    def test_local_first_chain(self):
        config = env_config(ollama=self.base_url(), KMW2_ALLOW_ECHO_FALLBACK="1")
        chain = route_chain(config)
        self.assertEqual(chain[0].name, "qwen-local")
        self.assertEqual(chain[-1].name, "echo")

    def test_fallback_to_echo(self):
        config = env_config(ollama="http://127.0.0.1:9", KMW2_ALLOW_ECHO_FALLBACK="1")
        with mock.patch("coding_worker_v2.providers.MuseCliProvider.is_available",
                        return_value=(False, "MOCK_ABSENT")):
            chain = route_chain(config)
        names = [p.name for p in chain]
        self.assertNotIn("qwen-local", names)
        self.assertIn("echo", names)
        gen, trace = generate_with_fallback([{"role": "user", "content": "ping"}],
                                            chain)
        self.assertEqual(gen.provider, "echo")
        self.assertTrue(trace[-1].ok)

    def test_empty_chain_explicit(self):
        with self.assertRaises(ProviderError) as ctx:
            generate_with_fallback([{"role": "user", "content": "x"}], [])
        self.assertEqual(ctx.exception.reason, "NO_PROVIDER_AVAILABLE")

    def test_discover_never_raises(self):
        config = env_config(ollama="http://127.0.0.1:9")
        rows = discover(config)
        self.assertEqual(len(rows), 5)
        by_name = {r["provider"]: r for r in rows}
        self.assertFalse(by_name["qwen-local"]["available"])
        self.assertFalse(by_name["deepseek-api"]["available"])


class TestDeepSeekAPI(FakeBackend):
    def test_disabled_by_default(self):
        config = env_config(ollama=self.base_url())
        allowed, reason = config.paid_api_allowed()
        self.assertFalse(allowed)
        self.assertEqual(reason, "API_NOT_ENABLED")

    def test_budget_required(self):
        config = env_config(ollama=self.base_url(), KMW2_DEEPSEEK_API_ENABLED="1",
                            KMW2_DEEPSEEK_API_MODEL="ds", KMW2_DEEPSEEK_PRICE_PER_1K_IN="1",
                            KMW2_DEEPSEEK_PRICE_PER_1K_OUT="2",
                            KMW2_DEEPSEEK_API_KEY="k")
        allowed, reason = config.paid_api_allowed()
        self.assertFalse(allowed)
        self.assertEqual(reason, "SPENDING_BUDGET_NOT_CONFIGURED")

    def test_pricing_required(self):
        config = env_config(ollama=self.base_url(), KMW2_DEEPSEEK_API_ENABLED="1",
                            KMW2_SPENDING_BUDGET_USD="5", KMW2_DEEPSEEK_API_MODEL="ds",
                            KMW2_DEEPSEEK_API_KEY="k")
        allowed, reason = config.paid_api_allowed()
        self.assertFalse(allowed)
        self.assertEqual(reason, "PRICING_UNCONFIGURED")

    def test_paid_call_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "spend.json"
            config = env_config(ollama=self.base_url(), KMW2_DEEPSEEK_API_ENABLED="1",
                                KMW2_SPENDING_BUDGET_USD="1000",
                                KMW2_DEEPSEEK_API_MODEL="ds-test",
                                KMW2_DEEPSEEK_API_BASE=self.base_url(),
                                KMW2_DEEPSEEK_PRICE_PER_1K_IN="100",
                                KMW2_DEEPSEEK_PRICE_PER_1K_OUT="200",
                                KMW2_DEEPSEEK_API_KEY="test-key-123")
            provider = DeepSeekAPIProvider(config, ledger)
            ok, _ = provider.is_available()
            self.assertTrue(ok)
            gen = provider.generate([{"role": "user", "content": "hi"}], max_tokens=10)
            self.assertEqual(gen.text, "hello-from-api")
            # 5 in * 0.1 + 3 out * 0.2 = 1.1
            import json as _json
            state = _json.loads(ledger.read_text())
            self.assertAlmostEqual(state["spent_usd"], 1.1)
            self.assertEqual(state["calls"], 1)

    def test_budget_exhaustion_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "spend.json"
            ledger.write_text('{"spent_usd": 9.99, "calls": 3}')
            config = env_config(ollama=self.base_url(), KMW2_DEEPSEEK_API_ENABLED="1",
                                KMW2_SPENDING_BUDGET_USD="5",
                                KMW2_DEEPSEEK_API_MODEL="ds",
                                KMW2_DEEPSEEK_API_BASE=self.base_url(),
                                KMW2_DEEPSEEK_PRICE_PER_1K_IN="100",
                                KMW2_DEEPSEEK_PRICE_PER_1K_OUT="200",
                                KMW2_DEEPSEEK_API_KEY="k")
            provider = DeepSeekAPIProvider(config, ledger)
            ok, reason = provider.is_available()
            self.assertFalse(ok)
            self.assertEqual(reason, "BUDGET_EXHAUSTED")


TWO_QWEN_TAGS = {"models": [
    {"name": "qwen2.5-coder:1.5b", "digest": "sha256:primary",
     "modified_at": "2026-01-01T00:00:00Z"},
    {"name": "qwen2.5-coder:0.5b", "digest": "sha256:fallback",
     "modified_at": "2026-01-01T00:00:00Z"},
]}


class TestQwenFallback(FakeBackend):
    def setUp(self):
        self._saved_tags = FakeState.tags
        FakeState.tags = TWO_QWEN_TAGS

    def tearDown(self):
        FakeState.tags = self._saved_tags

    def test_primary_preferred_with_fallback_advertised(self):
        config = env_config(ollama=self.base_url(),
                            KMW2_QWEN_MODEL="qwen2.5-coder:1.5b",
                            KMW2_QWEN_FALLBACK_MODEL="qwen2.5-coder:0.5b")
        provider = QwenProvider(config)
        ok, reason = provider.is_available()
        self.assertTrue(ok, reason)
        self.assertIn("MODEL=qwen2.5-coder:1.5b", reason)
        self.assertIn("FALLBACK=qwen2.5-coder:0.5b", reason)
        self.assertEqual(provider.capabilities().model, "qwen2.5-coder:1.5b")

    def test_fallback_used_when_primary_missing(self):
        config = env_config(ollama=self.base_url(),
                            KMW2_QWEN_MODEL="qwen2.5-coder:99b",
                            KMW2_QWEN_FALLBACK_MODEL="qwen2.5-coder:0.5b")
        provider = QwenProvider(config)
        ok, reason = provider.is_available()
        self.assertTrue(ok, reason)
        self.assertEqual(provider.capabilities().model, "qwen2.5-coder:0.5b")
        gen = provider.generate([{"role": "user", "content": "hi"}])
        self.assertEqual(gen.model, "qwen2.5-coder:0.5b")

    def test_both_missing_still_explicit(self):
        config = env_config(ollama=self.base_url(),
                            KMW2_QWEN_MODEL="nope:1b",
                            KMW2_QWEN_FALLBACK_MODEL="nope:2b")
        ok, reason = QwenProvider(config).is_available()
        self.assertFalse(ok)
        self.assertEqual(reason, "MODEL_NOT_IN_INVENTORY")

    def test_failover_on_primary_chat_error(self):
        from coding_worker_v2.providers import Generation
        config = env_config(ollama=self.base_url(),
                            KMW2_QWEN_MODEL="qwen2.5-coder:1.5b",
                            KMW2_QWEN_FALLBACK_MODEL="qwen2.5-coder:0.5b")
        provider = QwenProvider(config)
        fallback_gen = Generation(text='{"edits": []}', provider="qwen-local",
                                  model="qwen2.5-coder:0.5b")
        with mock.patch.object(QwenProvider, "_chat", autospec=True,
                               side_effect=[ProviderError("UNAVAILABLE", "boom"),
                                             fallback_gen]) as chat:
            gen = provider.generate([{"role": "user", "content": "hi"}])
        self.assertEqual(gen.model, "qwen2.5-coder:0.5b")
        attempted = [call.args[1] for call in chat.call_args_list]
        self.assertEqual(attempted, ["qwen2.5-coder:1.5b", "qwen2.5-coder:0.5b"])

    def test_all_models_failed_explicit(self):
        config = env_config(ollama=self.base_url(),
                            KMW2_QWEN_MODEL="qwen2.5-coder:1.5b",
                            KMW2_QWEN_FALLBACK_MODEL="qwen2.5-coder:0.5b")
        with mock.patch.object(QwenProvider, "_chat", autospec=True,
                               side_effect=ProviderError("TIMEOUT", "slow")):
            with self.assertRaises(ProviderError) as ctx:
                QwenProvider(config).generate([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.reason, "ALL_QWEN_MODELS_FAILED")

    def test_keep_alive_sent_to_ollama(self):
        from coding_worker_v2 import providers as providers_mod
        config = env_config(ollama=self.base_url(),
                            KMW2_QWEN_MODEL="qwen2.5-coder:0.5b")
        with mock.patch.object(providers_mod, "http_json",
                               wraps=providers_mod.http_json) as spy:
            QwenProvider(config).generate([{"role": "user", "content": "hi"}])
        chat_calls = [c for c in spy.call_args_list
                      if c.args and c.args[0].endswith("/api/chat")]
        self.assertTrue(chat_calls)
        self.assertEqual(chat_calls[0].args[1].get("keep_alive"), "5m")


if __name__ == "__main__":
    unittest.main()
