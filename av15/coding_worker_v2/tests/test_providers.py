"""Phase 1 tests: providers, routing, fallback, budget guard, redaction."""
import os
import json
import io
import urllib.error
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
from coding_worker_v2.providers import (ProviderResources, resource_headers, SpendLedger,
                                       Generation, Capabilities, http_json)
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.tasks import Task


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
            self.assertIsNone(gen.execution["actual_cost_usd"])
            self.assertAlmostEqual(gen.execution["estimated_cost_usd"], 1.1)
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

    def test_quota_errors_do_not_call_another_model_on_same_endpoint(self):
        config = env_config(ollama=self.base_url(),
                            KMW2_QWEN_MODEL="qwen2.5-coder:1.5b",
                            KMW2_QWEN_FALLBACK_MODEL="qwen2.5-coder:0.5b")
        for reason in ("RATE_LIMITED", "QUOTA_EXHAUSTED"):
            with self.subTest(reason=reason), \
                    mock.patch.object(QwenProvider, "_chat",
                                      side_effect=ProviderError(reason)) as chat:
                with self.assertRaisesRegex(ProviderError, reason):
                    QwenProvider(config).generate([])
                self.assertEqual(chat.call_count, 1)

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


class TestResourceControl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QueueStore(Path(self.tmp.name) / "q")
        self.now = 100.0
        self.resources = ProviderResources(self.store, clock=lambda: self.now)

    def test_disclosed_limits_and_unknown_are_distinct(self):
        self.assertEqual(resource_headers(None, now=100)["quota"], "UNKNOWN")
        row = resource_headers({"X-RateLimit-Remaining": "1",
                                "X-RateLimit-Limit": "20",
                                "Retry-After": "30"}, now=100)
        self.assertEqual(row["quota"], "LOW_QUOTA")
        self.assertEqual(row["retry_at"], 130)
        self.assertIsNone(row["reset_at"])

    def test_http_rate_limit_and_exhaustion_preserve_actual_headers(self):
        for payload, reason in (({}, "RATE_LIMITED"),
                                ({"error": {"code": "insufficient_quota"}},
                                 "QUOTA_EXHAUSTED")):
            with self.subTest(reason=reason):
                error = urllib.error.HTTPError(
                    "http://127.0.0.1/test", 429, "limited",
                    {"Retry-After": "30"}, io.BytesIO(json.dumps(payload).encode()))
                with mock.patch("coding_worker_v2.providers._NO_PROXY_OPENER.open",
                                side_effect=error):
                    with self.assertRaises(ProviderError) as caught:
                        http_json("http://127.0.0.1/test")
                self.assertEqual(caught.exception.reason, reason)
                self.assertIsNotNone(caught.exception.resources["retry_at"])

    def test_missing_api_usage_remains_unknown_and_reserved(self):
        config = env_config(KMW2_DEEPSEEK_API_ENABLED="1",
                            KMW2_SPENDING_BUDGET_USD="100",
                            KMW2_DEEPSEEK_API_MODEL="ds",
                            KMW2_DEEPSEEK_PRICE_PER_1K_IN="1",
                            KMW2_DEEPSEEK_PRICE_PER_1K_OUT="1",
                            KMW2_DEEPSEEK_API_KEY="test-key")
        provider = DeepSeekAPIProvider(config, Path(self.tmp.name) / "spend.json")
        with mock.patch("coding_worker_v2.providers.http_json", return_value={
                "choices": [{"message": {"content": "proposal"}}]}):
            gen = provider.generate([])
        self.assertIsNone(gen.tokens_in)
        self.assertIsNone(gen.execution["estimated_cost_usd"])
        self.assertIsNone(gen.execution["actual_cost_usd"])
        self.assertTrue(provider.ledger.read()["reservations"])

    def test_rate_limit_cooldown_survives_restart_then_resumes(self):
        self.resources.failed("qwen-local", ProviderError(
            "RATE_LIMITED", resources={"retry_at": 150, "source": "Retry-After"}))
        restarted = ProviderResources(self.store, clock=lambda: self.now)
        self.assertEqual(restarted.gate("qwen-local"), (False, "RATE_LIMITED", 150))
        provider = mock.Mock(name="provider")
        provider.name, provider.local = "qwen-local", True
        with self.assertRaises(ProviderError):
            generate_with_fallback([], [provider], resources=restarted)
        provider.capabilities.assert_not_called()
        self.now = 151
        self.assertTrue(restarted.gate("qwen-local")[0])

    def test_exhaustion_without_reset_never_guesses_resume(self):
        self.resources.failed("qwen-local", ProviderError("QUOTA_EXHAUSTED"))
        self.now += 100000
        self.assertEqual(self.resources.gate("qwen-local"), (False, "EXHAUSTED", None))

    def test_local_failure_switches_only_to_authorized_subscription(self):
        task = Task.from_dict({
            "mission_id": "M", "task_id": "switch", "owner_node": "AV15",
            "input": {"repo": self.tmp.name, "goal": "fix", "hints": {
                "external_authorized": True, "billing_mode": "existing-subscription",
                "max_provider_dispatches": 1}}})
        self.store.enqueue(task)
        task = self.store.claim("switch")
        local, external = mock.Mock(), mock.Mock()
        for provider, name, local_flag in ((local, "qwen-local", True),
                                           (external, "copilot-cli", False)):
            provider.name, provider.local = name, local_flag
            provider.capabilities.return_value = Capabilities(name, "model", local_flag)
        local.generate.side_effect = ProviderError("RATE_LIMITED",
                                                   resources={"retry_at": 150})
        external.generate.return_value = Generation("{}", "copilot-cli", "model")
        result, trace = generate_with_fallback(
            [], [external, local], resources=self.resources, task=task)
        self.assertEqual(result.provider, "copilot-cli")
        self.assertEqual([t.provider for t in trace], ["qwen-local", "copilot-cli"])
        self.assertEqual(task.task_id, "switch")
        checkpoint = self.store.load_checkpoint("switch")["data"]
        self.assertEqual(checkpoint["external_dispatches"], 1)
        with self.assertRaisesRegex(ProviderError, "EXTERNAL_DISPATCH_BUDGET_EXHAUSTED"):
            self.resources.invoke(external, task, [], 10, 10)
        self.assertEqual(external.generate.call_count, 1)

    def test_unauthorized_provider_is_never_invoked(self):
        task = Task.from_dict({
            "mission_id": "M", "task_id": "blocked", "owner_node": "AV15",
            "input": {"repo": self.tmp.name, "goal": "fix"}})
        provider = mock.Mock()
        provider.name, provider.local = "copilot-cli", False
        with self.assertRaisesRegex(ProviderError, "EXTERNAL_NOT_AUTHORIZED"):
            self.resources.invoke(provider, task, [], 10, 10)
        provider.generate.assert_not_called()

    def test_ambiguous_dispatch_is_not_replayed(self):
        task = Task.from_dict({
            "mission_id": "M", "task_id": "ambiguous", "owner_node": "AV15",
            "input": {"repo": self.tmp.name, "goal": "fix", "hints": {
                "external_authorized": True, "billing_mode": "existing-subscription",
                "max_provider_dispatches": 3}}})
        self.store.enqueue(task)
        task = self.store.claim(task.task_id)
        provider = mock.Mock()
        provider.name, provider.local = "copilot-cli", False
        provider.generate.side_effect = ProviderError("TIMEOUT")
        with self.assertRaisesRegex(ProviderError, "TIMEOUT"):
            self.resources.invoke(provider, task, [], 10, 10)
        self.now += 1000
        with self.assertRaisesRegex(ProviderError, "DISPATCH_RECONCILIATION_REQUIRED"):
            self.resources.invoke(provider, task, [], 10, 10)
        self.assertEqual(provider.generate.call_count, 1)

    def test_ledger_reserves_unknown_usage_and_rejects_corruption(self):
        path = Path(self.tmp.name) / "spend.json"
        ledger = SpendLedger(path)
        reference = ledger.reserve(4, 5)
        ledger.settle(reference, None)
        self.assertEqual(ledger.read()["spent_usd"], 0)
        with self.assertRaisesRegex(ProviderError, "BUDGET_EXCEEDED"):
            ledger.reserve(2, 5)
        path.write_text("{}")
        with self.assertRaisesRegex(ProviderError, "BUDGET_LEDGER_UNREADABLE"):
            ledger.reserve(1, 5)

    def test_zero_budget_api_does_not_dispatch(self):
        config = env_config(KMW2_DEEPSEEK_API_ENABLED="1",
                            KMW2_DEEPSEEK_API_MODEL="ds",
                            KMW2_DEEPSEEK_PRICE_PER_1K_IN="1",
                            KMW2_DEEPSEEK_PRICE_PER_1K_OUT="1",
                            KMW2_DEEPSEEK_API_KEY="test-key")
        provider = DeepSeekAPIProvider(config, Path(self.tmp.name) / "spend.json")
        with mock.patch("coding_worker_v2.providers.http_json") as transport:
            with self.assertRaises(ProviderError):
                provider.generate([])
        transport.assert_not_called()


class TestAuthenticatedCapabilityBoundary(unittest.TestCase):
    """Trusted-middleware test doubles, never production auth qualification."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QueueStore(Path(self.tmp.name) / "q")
        self.resources = ProviderResources(self.store, clock=lambda: 1000)
        self.binding = {"mission_id": "G0-G1-MINIMUM-SELLABLE-DEMO",
            "task_id": "pilot-0c3fdc91ed2e2a9011c0cb1d-t1", "owner_node": "AV15",
            "source_sha": "1" * 40, "scope_digest": "sha256:" + "2" * 64,
            "endpoint": "https://fixture.invalid/mcp", "subject": "FIXTURE_TRANSPORT"}
        self.payload = {k: v for k, v in self.binding.items() if k not in {"endpoint", "subject"}}
        self.payload.update(schema="KMCW_CAPABILITY_EVIDENCE_V1", observed_at=990,
            expires_at=1040, candidates=[{"provider": "qwen-local", "available": True,
                "observed_at": 990, "node_id": "AV15", "node_online": True,
                "heartbeat_at": 990, "tested_capabilities": ["bounded-edit"],
                "capability_evidence_ref": "fixture:capability",
                "qualification_status": "PASS", "capability_evidence_sha256": "a" * 64,
                "runtime_artifact_sha256": "b" * 64,
                "context_limit_tokens": 4096, "security_permissions": ["repo:approved-paths"]}])

    def route(self, payload=None, auth_change=None):
        import hashlib
        raw = json.dumps(self.payload if payload is None else payload, sort_keys=True).encode()
        auth = {"authenticated": True, "subject": self.binding["subject"],
                "endpoint": self.binding["endpoint"], "permissions": ["capability:read"],
                "body_sha256": hashlib.sha256(raw).hexdigest(), **(auth_change or {})}
        return self.resources.select_authenticated_advisory(raw, binding=self.binding,
            authentication=auth, capability="bounded-edit", context_tokens=100,
            required_permissions=["repo:approved-paths"])

    def test_auth_subject_permissions_endpoint_and_body_are_bound(self):
        for change in ({"authenticated": False}, {"subject": "other"},
                       {"permissions": ["capability:read", "admin"]},
                       {"endpoint": "https://other.invalid/mcp"}, {"body_sha256": "0" * 64}):
            with self.subTest(change=change), self.assertRaisesRegex(
                    ProviderError, "AUTHENTICATION_REQUIRED"):
                self.route(auth_change=change)

    def test_task_source_scope_and_payload_auth_claims_cannot_drift(self):
        for field in ("mission_id", "task_id", "source_sha", "scope_digest", "owner_node"):
            with self.subTest(field=field), self.assertRaisesRegex(ProviderError, "SCOPE_MISMATCH"):
                self.route({**self.payload, field: "other"})
        with self.assertRaisesRegex(ProviderError, "SCOPE_MISMATCH"):
            self.route({**self.payload, "authenticated": True})

    def test_offline_node_unavailable_provider_and_expired_evidence_rejected(self):
        candidate = self.payload["candidates"][0]
        for changes, expected in (({"node_online": False}, "OFFLINE_OR_STALE_NODE"),
                                  ({"qualification_status": "PROPOSED"}, "CAPABILITY_QUALIFICATION_UNPROVEN"),
                                  ({"runtime_artifact_sha256": "missing"}, "CAPABILITY_QUALIFICATION_UNPROVEN"),
                                  ({"available": False}, "AVAILABILITY_STALE_OR_UNPROVEN")):
            result = self.route({**self.payload, "candidates": [{**candidate, **changes}]})
            self.assertIsNone(result["selected_provider"])
            self.assertEqual(result["rejected"][0]["reason"], expected)
        with self.assertRaisesRegex(ProviderError, "EXPIRED_OR_STALE"):
            self.route({**self.payload, "expires_at": 999})

    def test_duplicate_advice_has_one_key_and_zero_dispatch_or_writes(self):
        from unittest import mock
        before = {str(p): p.read_bytes() for p in self.store.base.rglob('*') if p.is_file()}
        with mock.patch("coding_worker_v2.providers.http_json") as http, \
                mock.patch("subprocess.Popen") as process:
            first, second = self.route(), self.route()
            self.assertEqual(first, second)
            self.assertEqual(first["selected_provider"], "qwen-local")
            self.assertFalse(first["dispatch"])
            self.assertEqual(first["central_sync"], "UNSYNCED")
            http.assert_not_called(); process.assert_not_called()
        self.assertEqual({str(p): p.read_bytes() for p in self.store.base.rglob('*') if p.is_file()}, before)

    def test_external_spending_and_duplicate_provider_cannot_be_injected(self):
        candidate = {**self.payload["candidates"][0], "provider": "codex-cli",
                     "quota": "AVAILABLE", "quota_observed_at": 990,
                     "quota_evidence_ref": "fixture:quota", "estimated_cost_usd": 0}
        result = self.route({**self.payload, "candidates": [candidate]})
        self.assertIsNone(result["selected_provider"])
        self.assertEqual(result["rejected"][0]["reason"], "EXTERNAL_PROVIDER_NOT_APPROVED")
        with self.assertRaisesRegex(ProviderError, "CANDIDATE_INVALID"):
            self.route({**self.payload, "candidates": [candidate, candidate]})


class TestAdvisoryProviderSelection(unittest.TestCase):
    """Synthetic evidence; selection is not live provider qualification."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = QueueStore(Path(self.tmp.name) / "queue")
        self.resources = ProviderResources(self.store, clock=lambda: 1000)

    def candidate(self, name, **extra):
        return {"provider": name, "available": True, "observed_at": 990,
                "tested_capabilities": ["bounded-edit"],
                "capability_evidence_ref": "fixture:capability",
                "security_permissions": ["repo:approved-paths"],
                "context_limit_tokens": 4096, "quota": "AVAILABLE",
                "quota_observed_at": 990, "quota_evidence_ref": "fixture:quota",
                "estimated_cost_usd": 0.1, **extra}

    def select(self, candidates, **extra):
        return self.resources.select_advisory(
            candidates, capability="bounded-edit", context_tokens=100,
            required_permissions=["repo:approved-paths"], **extra)

    def test_deterministic_then_local_before_approved_external(self):
        candidates = [self.candidate(n) for n in
                      ("codex-cli", "deepseek-local", "qwen-local", "deterministic")]
        result = self.select(candidates, approved_external_providers=["codex-cli"],
                             budget_usd=1)
        self.assertEqual(result["eligible_providers"],
                         ["deterministic", "qwen-local", "deepseek-local", "codex-cli"])
        self.assertEqual(result["selected_provider"], "deterministic")
        self.assertFalse(result["dispatch"])
        self.assertTrue(result["admission_required"])
        self.assertFalse(result["independent_verified"])
        result = self.select(candidates[0:3], approved_external_providers=["codex-cli"],
                             budget_usd=1)
        self.assertEqual(result["selected_provider"], "qwen-local")

    def test_external_budget_approval_and_muse_are_explicit(self):
        for name in ("codex-cli", "copilot-cli", "muse-cli", "deepseek-api"):
            with self.subTest(name=name):
                result = self.select([self.candidate(name)], budget_usd=1)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(result["rejected"][0]["reason"],
                                 "EXTERNAL_PROVIDER_NOT_APPROVED")
                result = self.select([self.candidate(name)],
                                     approved_external_providers=[name])
                self.assertEqual(result["rejected"][0]["reason"],
                                 "EXTERNAL_BUDGET_UNPROVEN")
                result = self.select([self.candidate(name)],
                                     approved_external_providers=[name], budget_usd=1)
                self.assertEqual(result["selected_provider"], name)

    def test_context_security_capability_and_freshness_fail_closed(self):
        cases = [({"context_limit_tokens": 99}, "CONTEXT_CAPACITY_UNPROVEN"),
                 ({"security_permissions": []}, "SECURITY_PERMISSION_MISSING"),
                 ({"tested_capabilities": []}, "TESTED_CAPABILITY_EVIDENCE_MISSING"),
                 ({"capability_evidence_ref": ""}, "TESTED_CAPABILITY_EVIDENCE_MISSING"),
                 ({"observed_at": 1001}, "AVAILABILITY_STALE_OR_UNPROVEN"),
                 ({"observed_at": 600}, "AVAILABILITY_STALE_OR_UNPROVEN"),
                 ({"available": False}, "AVAILABILITY_STALE_OR_UNPROVEN")]
        for extra, reason in cases:
            with self.subTest(extra=extra):
                result = self.select([self.candidate("qwen-local", **extra)])
                self.assertIsNone(result["selected_provider"])
                self.assertEqual(result["rejected"][0]["reason"], reason)

    def test_external_quota_must_be_fresh_and_evidenced(self):
        for extra in ({"quota": "UNKNOWN"}, {"quota_observed_at": 500},
                      {"quota_evidence_ref": ""}):
            with self.subTest(extra=extra):
                result = self.select([self.candidate("codex-cli", **extra)],
                                     approved_external_providers=["codex-cli"], budget_usd=1)
                self.assertEqual(result["rejected"][0]["reason"],
                                 "EXTERNAL_QUOTA_UNPROVEN")

    def test_persisted_cooldown_is_reused_after_restart(self):
        self.store.save_checkpoint(ProviderResources.KEY, "provider_resources", {
            "qwen-local": {"blocked_until": 1100, "status": "RATE_LIMITED"}})
        result = self.select([self.candidate("qwen-local")])
        self.assertEqual(result["rejected"][0]["reason"], "PROVIDER_COOLDOWN")
        self.resources = ProviderResources(QueueStore(self.store.base), clock=lambda: 1000)
        self.assertEqual(self.select([self.candidate("qwen-local")]), result)

    def test_no_probe_inference_dispatch_or_checkpoint_mutation(self):
        before = {str(p): p.read_bytes() for p in self.store.base.rglob("*") if p.is_file()}
        with mock.patch("coding_worker_v2.providers.http_json") as http, \
                mock.patch("subprocess.Popen") as process, \
                mock.patch.object(self.resources, "invoke") as invoke, \
                mock.patch.object(self.store, "save_checkpoint") as save:
            self.select([self.candidate("qwen-local")])
            for call in (http, process, invoke, save):
                call.assert_not_called()
        after = {str(p): p.read_bytes() for p in self.store.base.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_unknown_route_and_duplicate_candidate_rejected(self):
        result = self.select([self.candidate("copilot-cloud")])
        self.assertEqual(result["rejected"][0]["reason"], "UNSUPPORTED_EXECUTION_ROUTE")
        with self.assertRaisesRegex(ProviderError, "ADVISORY_PROVIDER_ID_INVALID"):
            self.select([self.candidate("qwen-local"), self.candidate("qwen-local")])


if __name__ == "__main__":
    unittest.main()
