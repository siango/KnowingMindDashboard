"""Local pilot contracts; fixtures/mocks do not establish live acceptance."""
import io
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from coding_worker_v2.agent import mcp_server, memory, pilot, tools
from coding_worker_v2.agent.km_main import build_parser
from coding_worker_v2.config import WorkerConfig
from coding_worker_v2.pilot_install import install, package, uninstall, SOURCE
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.receipts import write_receipt
from coding_worker_v2.tasks import Task
from coding_worker_v2.tests.test_agent_executor import fixture_repo


class CheckpointPreparationTest(unittest.TestCase):
    """Pure contract fixtures, not authenticated production writer evidence."""

    def setUp(self):
        self.binding = {
            "mission_id": "G0-G1-MINIMUM-SELLABLE-DEMO",
            "task_id": "pilot-0c3fdc91ed2e2a9011c0cb1d-t1",
            "owner_node": "AV15",
            "source_sha": "55a0ff55d718defa9365482540e025122aa0cdd0",
            "worker_source_sha": "e9cb91af05ab42066afc854dfc7b436ce173ca6b",
            "scope_digest": "sha256:c81a571e9f7437846f5bcc5c1db1096e04e22128b20ecda85e10f5b7d906a8e0",
            "plan_digest": "8598597ce5f050f8ec0995c078061fda02cd254da775d0171bbacebd47e813ef",
        }
        identity = hashlib.sha256(json.dumps(
            self.binding, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode()).hexdigest()
        self.principal = {"authenticated": True, "subject": "FIXTURE_ONLY",
                          "owner_node": "AV15", "checkpoint_id": identity,
                          "permissions": ["checkpoint:append"]}
        self.payload = {**self.binding, "status": "ADMIT_BLOCKED",
                        "qc": "NOT_RUN", "receipt": None,
                        "evidence_refs": [{"uri": "fixture:ledger",
                                           "sha256": hashlib.sha256(b"fixture").hexdigest()}]}

    def prepare(self, payload=None, principal=None, binding=None):
        return mcp_server.prepare_execution_checkpoint(
            self.payload if payload is None else payload,
            binding=self.binding if binding is None else binding,
            principal=self.principal if principal is None else principal)

    def test_identical_preparation_has_one_identity_and_no_side_effects(self):
        with mock.patch("subprocess.Popen") as process, \
                mock.patch("pathlib.Path.open") as file_open, \
                mock.patch("coding_worker_v2.queue_store.QueueStore.append_event") as append:
            first, second = self.prepare(), self.prepare(dict(self.payload))
            self.assertEqual(first, second)
            self.assertEqual(first["checkpoint_id"], self.principal["checkpoint_id"])
            self.assertEqual(first["central_sync"], "UNSYNCED")
            self.assertFalse(first["persisted"])
            self.assertFalse(first["independent_verified"])
            self.assertTrue(first["independent_verification_required"])
            process.assert_not_called()
            file_open.assert_not_called()
            append.assert_not_called()

    def test_missing_auth_overbroad_or_other_identity_denied(self):
        for change in ({"authenticated": False}, {"subject": ""},
                       {"permissions": ["checkpoint:append", "admin"]},
                       {"checkpoint_id": "another-task"}, {"owner_node": "P02"}):
            with self.subTest(change=change), self.assertRaisesRegex(
                    tools.ToolError, "CHECKPOINT_WRITER_NOT_AUTHORIZED"):
                self.prepare(principal={**self.principal, **change})

    def test_every_binding_field_is_enforced_without_alias_rewrite(self):
        for field in self.binding:
            with self.subTest(field=field), self.assertRaisesRegex(
                    tools.ToolError, "CHECKPOINT_SCOPE_MISMATCH"):
                self.prepare(payload={**self.payload, field: "different"})
        with self.assertRaisesRegex(tools.ToolError, "BINDING_UNAVAILABLE"):
            self.prepare(binding={})

    def test_no_admission_qc_or_receipt_promotion(self):
        for change in ({"status": "PASS"}, {"qc": "PASS"},
                       {"receipt": {"signed": True}}):
            with self.subTest(change=change), self.assertRaisesRegex(
                    tools.ToolError, "AUTHORITY_PROMOTION_DENIED"):
                self.prepare(payload={**self.payload, **change})

    def test_evidence_content_changes_key_without_changing_identity(self):
        first = self.prepare()
        second = self.prepare(payload={**self.payload, "evidence_refs": [
            {"uri": "fixture:ledger", "sha256": "a" * 64}]})
        self.assertEqual(first["checkpoint_id"], second["checkpoint_id"])
        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(first["record"]["evidence_refs"], self.payload["evidence_refs"])

    def test_hashes_duplicate_refs_and_caller_authority_fields_rejected(self):
        for refs in ([], [{"uri": "fixture:ledger", "sha256": "missing"}],
                     self.payload["evidence_refs"] * 2):
            with self.subTest(refs=refs), self.assertRaises(tools.ToolError):
                self.prepare(payload={**self.payload, "evidence_refs": refs})
        with self.assertRaisesRegex(tools.ToolError, "PAYLOAD_INVALID"):
            self.prepare(payload={**self.payload, "principal": self.principal})

    def test_preparation_is_not_exposed_as_a_writable_mcp_tool(self):
        registry = tools.build_default_registry()
        names = {entry["name"] for entry in mcp_server._schemas(registry)}
        self.assertNotIn("prepare_execution_checkpoint", names)
        with self.assertRaisesRegex(tools.ToolError, "PILOT_TOOL_NOT_EXPOSED"):
            mcp_server._call(registry, {"name": "prepare_execution_checkpoint"}, {})


class PilotTest(unittest.TestCase):
    def test_review_installer_exposes_registry_without_issuing_grants(self):
        parser=build_parser()
        result=install(parser.parse_args(['pilot','install','--workspace',self.repo,
            '--runtime-base',str(self.root/'runtime'),'--account-id','pilot-a',
            '--allowed-path','calc.py']))
        server=json.loads((Path(self.repo)/'.mcp.json').read_text())['mcpServers']['kmcw']
        self.assertEqual(len(server['tools']),67)
        self.assertEqual(set(server['tools']),set(self.reg.names()))
        self.assertTrue(set(pilot.TOOLS).issubset(server['tools']))
        ctx=pilot.load_context(result['config'])
        before=ctx['store'].health()
        with mock.patch.dict(self.reg._fns,{'queue_health':mock.Mock()}) as handlers:
            with self.assertRaisesRegex(tools.ToolError,'MCP_TOOL_SCOPE_DENIED'):
                mcp_server._call(self.reg,{'name':'queue_health','arguments':{}},ctx)
            handlers['queue_health'].assert_not_called()
        self.assertEqual(ctx['store'].health(),before)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = fixture_repo(self.root)
        self.config = {
            "schema": pilot.SCHEMA, "account_id": "pilot-a", "worker_id": "AV15",
            "workspace": self.repo, "runtime_base": str(self.root / "runtime"),
            "allowed_paths": ["calc.py"], "context_paths": ["calc.py", "test_calc.py"],
            "test_command": "python3 -m unittest", "provider": "qwen-local",
            "memory_ranking": "qc",
        }
        self.path = self.root / "config.json"
        self.path.write_text(json.dumps(self.config))
        patch = mock.patch.dict(os.environ, {}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        self.ctx = pilot.load_context(self.path)
        self.reg = tools.build_default_registry()

    def call(self, name, args):
        return self.reg.call(name, args, self.ctx)

    def submit(self, request_id="first", **extra):
        return self.call("pilot_submit", {
            "request_id": request_id, "goal": "fix arithmetic addition",
            "allowed_paths": ["calc.py"], "acceptance": ["addition tests pass"], **extra})

    def receipt(self, task_id="first", account="pilot-a", verified=False):
        task = Task.from_dict({
            "mission_id": "G0-G1-MINIMUM-SELLABLE-DEMO", "task_id": task_id,
            "owner_node": "AV15", "input": {
                "repo": self.repo, "goal": "fix arithmetic addition",
                "hints": {"account_id": account, "worker_id": "AV15",
                          "workspace": self.repo, "base_commit": memory.current_base_commit(self.repo)}}})
        task.status = "DONE"
        task.output = {"changed": ["calc.py"], "provider": "fixture"}
        if verified:
            task.evidence = [
                {"event": "qc_checked", "qc": {"passed": True, "independent_verified": True}},
                {"event": "independent_verification", "proof": {
                    "authenticated": True, "status": "PASS", "task_id": task_id,
                    "source_sha": task.task_input.hints["base_commit"], "signature_sha256": "a" * 64}}]
        task.lease_token = "fixture-only"
        archived = task.to_dict()
        archived["lease_token_sha256"] = hashlib.sha256(task.lease_token.encode()).hexdigest()
        self.ctx["store"]._write_json(self.ctx["store"].done / task_id / "task.json", archived)
        write_receipt(self.ctx["receipts_dir"], task)
        return task

    def test_submit_idempotent_after_new_process_and_changed_payload_conflicts(self):
        first = self.submit()
        self.ctx = pilot.load_context(self.path)
        second = self.submit()
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertFalse(second["created"])
        with self.assertRaisesRegex(tools.ToolError, "IDEMPOTENCY_CONFLICT"):
            self.submit(goal="fix a different problem")
        self.assertEqual(self.ctx["store"].health()["inbox"], 0)

    def test_decomposed_intake_is_idempotent_and_mcp_readiness_is_read_only(self):
        units = [{"task_id": name, "goal": "fix arithmetic addition",
                  "deps": deps, "allowed_paths": ["calc.py"],
                  "acceptance": ["addition tests pass"],
                  "input_contract": ["approved source"],
                  "output_contract": ["bounded patch and check result"],
                  "evidence_required": ["source SHA", "independent receipt"]}
                 for name, deps in (("t1", []), ("check", ["t1"]))]
        first = self.submit(work_units=units)
        second = self.submit(work_units=units)
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertFalse(second["created"])
        with mock.patch("coding_worker_v2.agent.executor.run_task") as dispatch:
            read = self.call("pilot_read", {"plan_id": first["plan_id"]})
            nodes = read["orchestration"]["nodes"]
            self.assertEqual([node["task_id"] for node in nodes], ["t1", "check"])
            self.assertTrue(all(not node["dispatch_authorized"] for node in nodes))
            self.assertTrue(all(node["task_scope_digest"] for node in nodes))
            self.assertIsNone(read["receipt"])
            self.assertEqual(self.ctx["store"].health()["inbox"], 0)
            dispatch.assert_not_called()
        self.ctx["store"].save_checkpoint(f".pilot-{first['plan_id']}", "pilot_run_readback",
                                          {"report": {"status": "BLOCKED"}})
        events = self.ctx["store"].events / f"{first['task_id']}.jsonl"
        before = events.read_bytes()
        held = self.submit(work_units=units)
        self.assertEqual(held["task_id"], first["task_id"])
        self.assertFalse(held["created"])
        self.assertEqual(held["status"], "CHECKPOINT_RETAINED")
        self.assertEqual(events.read_bytes(), before)
        with self.assertRaisesRegex(tools.ToolError, "ORIGINAL_TASK_ID_REQUIRED"):
            self.submit(request_id="bad-root", work_units=[{**units[0], "task_id": "other"}])

    def test_context_and_submission_deny_unapproved_or_protected_paths(self):
        for name, args in (
            ("pilot_context", {"paths": ["../config.json"]}),
            ("pilot_submit", {"request_id": "bad", "goal": "fix bug",
                              "allowed_paths": ["test_calc.py"], "acceptance": ["pass"]}),
        ):
            with self.assertRaises(tools.ToolError):
                self.call(name, args)
        self.assertIn("return a - b", self.call("pilot_context", {})["files"]["calc.py"])
        self.config["context_paths"] = [".git/config"]
        with self.assertRaises(tools.ToolError):
            pilot.validate_config(self.config)

    def test_symlink_context_rejected_after_install(self):
        path = Path(self.repo) / "calc.py"
        path.unlink()
        path.symlink_to(self.path)
        with self.assertRaises(tools.ToolError):
            self.call("pilot_context", {})

    def test_real_missing_auditor_blocks_before_dispatch_and_persists_readback(self):
        submitted = self.submit()
        result = self.call("pilot_run", {"plan_id": submitted["plan_id"]})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["admission_audit"]["reason"], "AUDITOR_UNAVAILABLE")
        events = self.call("pilot_events", {"task_id": submitted["task_id"]})
        steps = {(e["step"], e["status"]) for e in events["events"]}
        self.assertTrue({("gate.ADMIT", "BLOCKED"), ("learning.recorded", "UNPROVEN"),
                         ("plan.difficulty_frozen", "RECORDED"), ("task.outcome", "SKIPPED")} <= steps)
        self.assertNotIn("PASS", {e["status"] for e in events["events"]})
        first_count = events["event_count"]
        # Restart and replay the same blocked admission: no duplicate events.
        self.ctx = pilot.load_context(self.path)
        self.call("pilot_run", {"plan_id": submitted["plan_id"]})
        replay = self.call("pilot_events", {"task_id": submitted["task_id"]})
        self.assertEqual(replay["event_count"], first_count)
        self.assertTrue(replay["chain_intact"])
        self.ctx["account_id"] = "pilot-b"
        with self.assertRaisesRegex(tools.ToolError, "ACCOUNT_OR_PROJECT_DENIED"):
            self.call("pilot_events", {"task_id": submitted["task_id"]})
        self.ctx = pilot.load_context(self.path)
        self.ctx = pilot.load_context(self.path)
        read = self.call("pilot_read", {"plan_id": submitted["plan_id"]})
        self.assertEqual(read["run"]["status"], "BLOCKED")
        self.assertIsNone(read["receipt"])
        self.assertEqual(self.ctx["store"].health()["inbox"], 0)
        self.assertIn("return a - b", (Path(self.repo) / "calc.py").read_text())

    def test_second_job_waits_for_actual_verified_first_receipt(self):
        second = self.submit("second", after_task_id="first")
        args = {"plan_id": second["plan_id"]}
        with mock.patch("coding_worker_v2.agent.executor.execute_plan") as execute:
            self.assertEqual(self.call("pilot_run", args)["reason"], "PREDECESSOR_RECEIPT_MISSING")
            self.receipt()
            self.assertEqual(self.call("pilot_run", args)["reason"], "PREDECESSOR_NOT_VERIFIED")
            execute.assert_not_called()
            self.receipt(verified=True)
            execute.return_value = {"status": "BLOCKED"}
            self.call("pilot_run", args)
            self.assertEqual(execute.call_count, 1)

    def test_account_work_and_search_deny_foreign_account(self):
        self.receipt("foreign", "pilot-b", verified=True)
        with self.assertRaisesRegex(tools.ToolError, "ACCOUNT_OR_PROJECT_DENIED"):
            self.call("account_work", {"task_id": "foreign"})
        self.assertEqual(self.call("pilot_search", {"goal": "arithmetic"})["cases"], [])
        self.assertEqual(memory.classify(
            "fix arithmetic addition", self.repo, memory.current_base_commit(self.repo),
            self.ctx["store"], self.ctx["receipts_dir"], account_id="pilot-a").decision, "NEW")
        self.receipt("owned", verified=True)
        result = self.call("account_work", {"task_id": "owned"})
        self.assertTrue(result["verified"])
        self.assertEqual(result["payment_status"], "PAYMENT_UNVERIFIED")
        self.assertIsNone(result["verified_revenue"])
        self.assertEqual(self.call("pilot_search", {"goal": "arithmetic"})["cases"][0]["task_id"], "owned")

    def test_account_scope_cannot_read_foreign_plan(self):
        plan = self.submit()
        self.ctx["account_id"] = "pilot-b"
        with self.assertRaisesRegex(tools.ToolError, "ACCOUNT_OR_PROJECT_DENIED"):
            self.call("pilot_read", {"plan_id": plan["plan_id"]})

    def test_foreign_queue_is_not_monitored_or_retrieved(self):
        task = self.receipt("foreign", "pilot-b")
        task.task_id = "foreign-pending"
        task.status = "QUEUED"
        self.ctx["store"].enqueue(task)
        result = memory.classify(
            task.task_input.goal, self.repo, task.task_input.hints["base_commit"],
            self.ctx["store"], self.ctx["receipts_dir"], account_id="pilot-a")
        self.assertEqual(result.decision, "NEW")

    def test_usage_keeps_unknown_and_nonpassing_work_separate(self):
        task = self.receipt()
        data = {"status": "FAILED", "evidence": [
            {"event": "model_generated", "provider": "copilot-cli",
             "tokens_in": 100, "tokens_out": None, "latency_ms": 23}]}
        usage = pilot.usage(data)
        self.assertIsNone(usage["tokens_in"])
        self.assertEqual(usage["token_basis"], "UNKNOWN_FOR_copilot-cli")
        self.assertIsNone(usage["known_actual_cost_usd"])
        self.assertEqual(usage["outcome_bucket"], "NOT_QC_PASSED")
        self.assertEqual(usage["external_generations"], 1)

    def test_usage_never_reports_echo_word_counts_as_tokens(self):
        data = {"status": "DONE", "evidence": [
            {"event": "model_generated", "provider": "qwen-local", "tokens_in": 40, "tokens_out": 9},
            {"event": "model_generated", "provider": "echo", "tokens_in": 17, "tokens_out": 8}]}
        usage = pilot.usage(data)
        self.assertIsNone(usage["tokens_in"])
        self.assertEqual(usage["token_basis"], "UNKNOWN_FOR_echo")
        self.assertEqual(usage["tokens_measured_partial"],
                         {"generations": 1, "tokens_in": 40, "tokens_out": 9})
        measured = pilot.usage({"evidence": data["evidence"][:1]})
        self.assertEqual((measured["tokens_in"], measured["token_basis"]), (40, "PROVIDER_REPORTED"))
        echo_only = pilot.usage({"evidence": data["evidence"][1:]})
        self.assertIsNone(echo_only["buckets"]["actual"]["tokens_in"])

    def test_correlation_is_stable_on_resume_and_duplicates_are_not_double_counted(self):
        from coding_worker_v2.run_once import usage_correlation
        task = self.receipt("corr")
        execution = {"usage_source": "COPILOT_CLI_USAGE_OUTPUT_FILE"}
        first = usage_correlation(task, 0, "copilot-cli", "m", execution)
        self.assertEqual(first, usage_correlation(task, 0, "copilot-cli", "m", execution))
        self.assertNotEqual(first["idempotency_key"],
                            usage_correlation(task, 1, "copilot-cli", "m", execution)["idempotency_key"])
        self.assertEqual((first["account_id"], first["task_id"], first["usage_scope"]),
                         ("pilot-a", "corr", "CUSTOMER_TASK"))
        native = {"usage_source": "COPILOT_CLI_USAGE_OUTPUT_FILE", "premium_requests": 1,
                  "nano_aiu": 10, "tokens_in": 5, "tokens_out": 2}
        event = {"event": "model_generated", "provider": "copilot-cli", "tokens_in": 5,
                 "tokens_out": 2, "native_usage": native, "reservation_ref": None, **first}
        failed = {"event": "provider_paused", "usage": dict(native, tokens_in=3, tokens_out=1),
                  **usage_correlation(task, 1, "failed-dispatch", "-", native)}
        result = pilot.usage({"evidence": [event, dict(event), failed, dict(failed)]})
        self.assertEqual(result["duplicate_generation_events_ignored"], 2)
        actual = result["buckets"]["actual"]
        self.assertEqual((actual["tokens_in"], actual["copilot_premium_requests"]), (5, 1))
        self.assertEqual(actual["failed_dispatch_tokens_in"], 3)
        self.assertIsNone(actual["billed_cost_usd"])

    def test_reservation_and_estimate_are_never_billed(self):
        event = {"event": "model_generated", "provider": "deepseek-api", "tokens_in": 10,
                 "tokens_out": 4, "usage_source": "OPENAI_COMPATIBLE_USAGE_FIELDS",
                 "estimated_cost_usd": 0.002, "actual_cost_usd": None, "reservation_ref": "r1"}
        buckets = pilot.usage({"evidence": [event]})["buckets"]
        self.assertEqual(buckets["estimated"]["cost_usd"], 0.002)
        self.assertIsNone(buckets["actual"]["billed_cost_usd"])
        self.assertEqual(buckets["reservations"], "BUDGET_HOLDS_EXCLUDED_NEVER_BILLED")

    def test_orchestration_usage_is_separate_scope(self):
        path = self.root / "usage.json"
        path.write_text(json.dumps({"aiCreditsStatus": "complete", "totalPremiumRequestCost": 1,
                                    "totalNanoAiu": 7, "modelMetrics": {"m": {"usage": {
                                        "inputTokens": 9, "outputTokens": 3}}}}))
        result = pilot.orchestration_usage(path, "pilot-abc")
        self.assertEqual(result["usage_scope"], "ORCHESTRATION_NOT_CUSTOMER_TASK")
        self.assertEqual((result["tokens_in"], result["premium_requests"]), (9, 1))
        self.assertIsNone(result["billed_cost_usd"])
        with self.assertRaises(tools.ToolError):
            pilot.orchestration_usage(path, "../bad")

    def test_benchmark_uses_labels_and_keeps_nonpassing_usage_separate(self):
        from coding_worker_v2.pilot_benchmark import benchmark
        self.receipt("verified", verified=True)
        self.receipt("not-verified")
        result = benchmark(
            self.repo, self.ctx["store"], self.ctx["receipts_dir"],
            [{"goal": "arithmetic", "relevant_task_ids": ["verified"]}],
            account_id="pilot-a", rounds=2)
        self.assertEqual(result["measurements"]["qc"]["hit_at_5"], 1)
        self.assertEqual(result["historical_usage_by_outcome"]["QC_PASSED"][0]["task_id"], "verified")
        self.assertEqual(result["historical_usage_by_outcome"]["NOT_QC_PASSED"][0]["task_id"], "not-verified")
        self.assertIsNone(result["tokens_per_live_qc_pass"])

    def test_mcp_handshake_errors_allowlist_and_durable_audit(self):
        requests = [
            {"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "git_push", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "pilot_context", "arguments": {}}},
        ]
        sink = io.StringIO()
        mcp_server.serve(self.ctx, io.BytesIO(
            ("\n".join(json.dumps(r) for r in requests) + "\n").encode()), sink)
        responses = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(len(responses), 5)
        self.assertIn("error", responses[0])
        self.assertEqual({t["name"] for t in responses[2]["result"]["tools"]}, set(tools.build_default_registry().names()))
        self.assertTrue(responses[3]["result"]["isError"])
        self.assertFalse(responses[4]["result"]["isError"])
        events = self.ctx["store"].load_checkpoint(".tool-audit")["data"]["events"]
        self.assertTrue(any(e.get("event") == "tool_called" for e in events))

    def test_mcp_invalid_json_and_oversize_fail_closed(self):
        sink = io.StringIO()
        mcp_server.serve(self.ctx, io.BytesIO(b'[]\nnot-json\n'), sink)
        self.assertEqual([json.loads(r)["error"]["code"] for r in sink.getvalue().splitlines()],
                         [-32600, -32700])
        self.assertEqual(mcp_server.serve(self.ctx, io.BytesIO(b"x" * 1_000_001), sink), 1)

    def test_install_uninstall_preserves_other_mcp_and_runtime(self):
        mcp = Path(self.repo) / ".mcp.json"
        mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "existing"}}}))
        parser = build_parser()
        args = parser.parse_args([
            "pilot", "install", "--workspace", self.repo, "--runtime-base", str(self.root / "runtime"),
            "--account-id", "pilot-a", "--allowed-path", "calc.py"])
        result = install(args)
        self.assertTrue(result["installed"])
        context = pilot.load_context(result["config"])
        task = self.receipt()
        uninstall(parser.parse_args(["pilot", "uninstall", "--workspace", self.repo]))
        self.assertEqual(json.loads(mcp.read_text()), {"mcpServers": {"other": {"command": "existing"}}})
        self.assertTrue((context["receipts_dir"] / f"{task.task_id}.json").exists())

    def test_uninstall_refuses_changed_files(self):
        parser = build_parser()
        install(parser.parse_args([
            "pilot", "install", "--workspace", self.repo, "--runtime-base", str(self.root / "runtime"),
            "--account-id", "pilot-a", "--allowed-path", "calc.py"]))
        agent = Path(self.repo) / ".github/agents/kmcw.agent.md"
        agent.write_text("owner edits")
        with self.assertRaises(ValueError):
            uninstall(parser.parse_args(["pilot", "uninstall", "--workspace", self.repo]))
        self.assertTrue((Path(self.repo) / ".mcp.json").exists())

    def test_clean_archive_install_and_stdio_in_new_interpreter(self):
        archive = self.root / "internal.zip"
        parser = build_parser()
        package(parser.parse_args(["pilot", "package", "--output", str(archive)]))
        source = self.root / "clean-source"
        with zipfile.ZipFile(archive) as packed:
            names = packed.namelist()
            self.assertFalse(any(".env" in name or "queue/" in name or ".key" in name for name in names))
            self.assertIn("DISTRIBUTION.json", names)
            packed.extractall(source)
        (source / "km").chmod(0o755)
        venv = self.root / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True,
                       capture_output=True, timeout=30)
        argv = [str(venv / "bin/python"), "-m", "coding_worker_v2.agent.km_main"]
        clean_env = {"PATH": os.environ["PATH"], "HOME": str(self.root / "clean-home"),
                     "PYTHONUTF8": "1", "KMW2_OWNER_NODE": "AV15"}
        installed = subprocess.run(argv + [
            "pilot", "install", "--workspace", self.repo, "--runtime-base", str(self.root / "clean-runtime"),
            "--account-id", "clean-account", "--allowed-path", "calc.py"],
            cwd=source, env=clean_env, capture_output=True, text=True, timeout=30, check=True)
        config = json.loads(installed.stdout)["config"]
        first = subprocess.run(argv + [
            "pilot", "call", "pilot_submit", "--config", config, "--args", json.dumps({
                "request_id": "restart", "goal": "fix addition", "allowed_paths": ["calc.py"],
                "acceptance": ["test addition"]})],
            cwd=source, env=clean_env, capture_output=True, text=True, timeout=30, check=True)
        submitted = json.loads(first.stdout)
        second = subprocess.run(argv + [
            "pilot", "call", "pilot_submit", "--config", config, "--args", json.dumps({
                "request_id": "restart", "goal": "fix addition", "allowed_paths": ["calc.py"],
                "acceptance": ["test addition"]})],
            cwd=source, env=clean_env, capture_output=True, text=True, timeout=30, check=True)
        self.assertFalse(json.loads(second.stdout)["created"])
        self.assertEqual(submitted["task_id"], json.loads(second.stdout)["task_id"])
        wire = '\n'.join(json.dumps(r) for r in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})) + "\n"
        transport = subprocess.run(argv + ["pilot", "mcp", "--config", config],
                                   cwd=source, env=clean_env, input=wire, capture_output=True,
                                   text=True, timeout=30, check=True)
        self.assertEqual(len(json.loads(transport.stdout.splitlines()[1])["result"]["tools"]), 67)
