"""V3 tool router tests: discovery, validation, permissions, safe exec."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import tools  # noqa: E402
from coding_worker_v2.agent.tools import ToolError, ToolRegistry, ToolSpec  # noqa: E402
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.queue_store import QueueStore  # noqa: E402


class TestRegistry(unittest.TestCase):
    def test_runtime_memory_precedes_handler_and_selection_is_audited(self):
        from coding_worker_v2.agent import memory
        case={'provenance':{'original_ref':'retained-observation','sha256':'a'*64},
              'retrieval_only':True,'verified_for_current_source':False}
        def inspect(args,ctx):
            self.assertEqual(ctx['work_memory'],[case])
            self.assertEqual(ctx['evidence'][-1]['event'],'tool_selected')
            return {'observed':True}
        self.reg._fns['repo_inspect']=inspect
        with mock.patch.object(memory,'retrieve_work',return_value=[case]) as retrieve:
            result=self.reg.call('repo_inspect',{'repo':self.ctx['workspace']},self.ctx)
        self.assertEqual(result,{'observed':True})
        retrieve.assert_called_once()
        events=self.ctx['evidence']
        self.assertEqual([e['event'] for e in events[-3:]],
                         ['memory_retrieved','tool_selected','tool_called'])
        self.assertFalse(events[-3]['grants_authority'])
        self.assertTrue(events[-1]['ok'])

    def test_denied_runtime_call_cannot_retrieve_or_retain_memory(self):
        from coding_worker_v2.agent import memory
        self.ctx.update(command_auth={},tool_bindings={},work_memory=['stale'])
        handler=mock.Mock(); self.reg._fns['queue_health']=handler
        with mock.patch.object(memory,'retrieve_work') as retrieve:
            with self.assertRaisesRegex(ToolError,'MCP_TOOL_SCOPE_DENIED'):
                self.reg.call('queue_health',{},self.ctx,mcp=True)
        retrieve.assert_not_called();handler.assert_not_called()
        self.assertNotIn('work_memory',self.ctx)

    def test_mcp_tool_grant_does_not_imply_receipt_memory_grant(self):
        from coding_worker_v2.agent import memory, capabilities
        self.reg._fns['repo_inspect']=lambda args,ctx:{'read':True}
        with mock.patch.object(capabilities,'authorized',return_value=True), \
             mock.patch.object(memory,'retrieve_work') as retrieve:
            self.assertEqual(self.reg.call('repo_inspect',
                {'repo':self.ctx['workspace']},self.ctx,mcp=True),{'read':True})
        retrieve.assert_not_called()

    def test_reuse_callable_client_handler_metadata_does_not_replace_client(self):
        class ExistingCallableReader:
            def __call__(self,args,ctx):return {'binding_retained':True}
        client=ExistingCallableReader()
        self.reg._specs.pop('queue_health');self.reg._fns.pop('queue_health')
        self.reg.register(ToolSpec('queue_health','existing fixture callable'),client)
        self.assertIn('ExistingCallableReader',self.reg._specs['queue_health'].source_ref)
        row=next(r for r in self.reg.discover(self.ctx) if r['tool']=='queue_health')
        self.assertTrue(row['available'])
        self.assertIs(self.reg._fns['queue_health'],client)
        self.assertEqual(self.reg.call('queue_health',{},self.ctx),{'binding_retained':True})

    def test_reuse_plan_and_run_validation_keep_original_registry(self):
        import argparse
        from coding_worker_v2.agent import km_main
        ctx={**self.ctx,'tool_registry':self.reg,
             'owner_node':self.ctx['config'].owner_node,'state_dir':self.root/'state'}
        with mock.patch.object(km_main.agent_tools,'build_default_registry',
                side_effect=AssertionError('must preserve bound registry')), \
             mock.patch.object(km_main.agent_planner,'simple_plan',
                side_effect=RuntimeError('STOP_BEFORE_PLAN_WRITE')), \
             self.assertRaisesRegex(RuntimeError,'STOP_BEFORE_PLAN_WRITE'):
            km_main.cmd_plan(argparse.Namespace(command='Inspect existing source',
                plan_id='isolated-validation',revise=None,feedback=None,simple=True,mission=None),ctx)
        path=self.root/'validation-only.json';path.write_text('{}')
        with mock.patch.object(km_main.agent_tools,'build_default_registry',
                side_effect=AssertionError('must preserve bound registry')), \
             mock.patch.object(km_main.agent_planner,'validate_plan_dict',
                side_effect=RuntimeError('STOP_BEFORE_EXECUTOR')) as validate, \
             self.assertRaisesRegex(RuntimeError,'STOP_BEFORE_EXECUTOR'):
            km_main.cmd_run(argparse.Namespace(provider=None,plan=str(path)),ctx)
        validate.assert_called_once_with({},self.reg.names())
        self.assertFalse((self.root/'state').exists())

    def test_replay_bound_client_handler_is_not_replaced(self):
        import argparse,contextlib,io
        from coding_worker_v2.agent import km_main
        original_client=self.ctx['config']
        original_session=self.ctx['store']
        handler_calls=[]
        def bound_read(arguments,forwarded):
            self.assertIs(forwarded['config'],original_client)
            self.assertIs(forwarded['store'],original_session)
            handler_calls.append(arguments)
            return {'counts':original_session.health(),'binding_retained':True}
        self.reg._fns['queue_health']=bound_read
        ctx={**self.ctx,'tool_registry':self.reg,'read_only':True}
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(km_main.cmd_tools(
                argparse.Namespace(call='queue_health',args='{}',yes=False),ctx),0)
        result=json.loads(output.getvalue())['result']
        self.assertIs(result.get('binding_retained'),True)
        self.assertEqual(len(handler_calls),1)
        self.assertIs(self.reg._fns['queue_health'],bound_read)
        ctx['command_auth']={};ctx['tool_bindings']={}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(km_main.cmd_tools(
                argparse.Namespace(call='queue_health',args='{}',yes=False),ctx),1)
        self.assertEqual(len(handler_calls),1)
        self.assertIs(ctx['config'],original_client)
        self.assertIs(ctx['store'],original_session)

    def test_reuse_cli_preserves_supplied_registry_and_its_acl(self):
        import argparse
        from coding_worker_v2.agent import km_main
        ctx={**self.ctx,'tool_registry':self.reg,'read_only':True}
        with mock.patch.object(km_main.agent_tools,'build_default_registry',
                side_effect=AssertionError('must reuse original registry')), \
             mock.patch('builtins.print'):
            self.assertEqual(km_main.cmd_tools(
                argparse.Namespace(call='queue_health',args='{}',yes=False),ctx),0)
            ctx['command_auth']={};ctx['tool_bindings']={}
            self.assertEqual(km_main.cmd_tools(
                argparse.Namespace(call='queue_health',args='{}',yes=False),ctx),1)
        events=self.ctx['store'].load_checkpoint('.tool-audit')['data']['events']
        self.assertEqual(events[-1]['error_class'],'MCP_TOOL_SCOPE_DENIED')

    def test_cli_preserves_security_context_and_requests_scoped_registry_checks(self):
        import argparse
        from coding_worker_v2.agent import km_main
        ctx = {**self.ctx, 'command_auth': {}, 'command_channels': {},
               'tool_bindings': {}, 'account_id': 'isolated-audit',
               'mission_id': 'isolated-mission', 'task_id': 'isolated-task',
               'read_only': True}
        args = argparse.Namespace(call='queue_health', args='{}', yes=False)
        def denied(name, arguments, forwarded, *, mcp):
            self.assertTrue(mcp)
            for field in ('store','command_auth','command_channels','tool_bindings'):
                self.assertIs(forwarded[field], ctx[field])
            for field in ('account_id','mission_id','task_id','read_only'):
                self.assertEqual(forwarded[field], ctx[field])
            raise ToolError('MCP_TOOL_SCOPE_DENIED')
        with mock.patch.object(km_main.agent_tools,'build_default_registry',return_value=self.reg), \
             mock.patch.object(self.reg,'call',side_effect=denied), \
             mock.patch('builtins.print'):
            self.assertEqual(km_main.cmd_tools(args,ctx),1)

    def test_cli_unqualified_context_cannot_reach_queue_handler(self):
        import argparse
        from coding_worker_v2.agent import km_main
        ctx = {**self.ctx, 'command_auth': {}, 'tool_bindings': {}}
        handler = mock.Mock()
        self.reg._fns['queue_health'] = handler
        before = self.ctx['store'].health()
        with mock.patch.object(km_main.agent_tools,'build_default_registry',return_value=self.reg), \
             mock.patch('builtins.print'):
            self.assertEqual(km_main.cmd_tools(
                argparse.Namespace(call='queue_health',args='{}',yes=False),ctx),1)
        handler.assert_not_called()
        self.assertEqual(self.ctx['store'].health(),before)
        events = self.ctx['store'].load_checkpoint('.tool-audit')['data']['events']
        self.assertEqual(events[-1]['error_class'],'MCP_TOOL_SCOPE_DENIED')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "repo").mkdir()
        (self.root / "repo" / "a.py").write_text("print('needle')\n")
        self.reg = tools.build_default_registry()
        self.ctx = {"workspace": str(self.root / "repo"),
                    "store": QueueStore(self.root / "q"),
                    "receipts_dir": str(self.root / "receipts"),
                    "config": WorkerConfig.from_env(),
                    "evidence": []}

    def tearDown(self):
        self.tmp.cleanup()

    def test_discover_never_raises(self):
        rows = self.reg.discover({})
        self.assertTrue(len(rows) >= 10)
        names = {r["tool"] for r in rows}
        for expected in ("repo_inspect", "code_search", "git_diff",
                         "task_enqueue", "git_commit", "pr_create"):
            self.assertIn(expected, names)

    def test_command_contract_is_stable_and_schema_checked(self):
        import hashlib
        first = self.reg.command_contract()
        second = self.reg.command_contract()
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "KMCW_TOOL_COMMAND_CONTRACT_V1")
        self.assertEqual(first["command_count"], len(self.reg.names()))
        self.assertEqual([item["name"] for item in first["commands"]],
                         self.reg.names())
        canonical = json.dumps(first["commands"], sort_keys=True,
                               separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(first["sha256"],
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        for item in first["commands"]:
            self.assertIn(item["permission"], ("read", "write", "destructive"))
            self.assertIn("side_effects", item)
            self.assertIn("required", item)
            self.assertIn("optional", item)

    def test_duplicate_or_ambiguous_commands_rejected(self):
        with self.assertRaises(ToolError) as error:
            self.reg.register(ToolSpec("repo_inspect", "duplicate"), lambda a, c: {})
        self.assertEqual(error.exception.reason, "TOOL_SPEC_INVALID")
        with self.assertRaises(ToolError):
            self.reg.register(ToolSpec("ambiguous", "invalid",
                                       {"repo": "str"}, {"repo": "str"}),
                              lambda a, c: {})

    def test_tool_evidence_uses_current_call_not_stale_success(self):
        calls = [0]

        def one_pass_then_fail(_args, _ctx):
            calls[0] += 1
            if calls[0] == 1:
                return {"ok": True}
            raise ToolError("SYNTHETIC_FAILURE")

        self.reg.register(ToolSpec("one_pass", "regression probe"),
                          one_pass_then_fail)
        self.reg.call("one_pass", {}, self.ctx)
        with self.assertRaises(ToolError):
            self.reg.call("one_pass", {}, self.ctx)
        events = [e for e in self.ctx["evidence"]
                  if e.get("event") == "tool_called" and e.get("tool") == "one_pass"]
        self.assertEqual([e["ok"] for e in events], [True, False])
        self.assertNotIn("_one_pass_ok", self.ctx)

    def test_non_dict_result_is_failure_not_success(self):
        self.reg.register(ToolSpec("bad_result", "regression probe"),
                          lambda _a, _c: "not a result dict")
        with self.assertRaises(ToolError) as error:
            self.reg.call("bad_result", {}, self.ctx)
        self.assertEqual(error.exception.reason, "TOOL_RESULT_INVALID")
        self.assertFalse(self.ctx["evidence"][-1]["ok"])
        self.assertNotIn("_bad_result_ok", self.ctx)

    def test_unknown_tool_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("nope_tool", {}, self.ctx)
        self.assertEqual(ctx.exception.reason, "TOOL_UNKNOWN")

    def test_missing_arg_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("code_search", {"repo": "x"}, self.ctx)
        self.assertEqual(ctx.exception.reason, "TOOL_ARG_MISSING")

    def test_unknown_arg_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("repo_inspect", {"repo": "x", "bogus": 1}, self.ctx)
        self.assertEqual(ctx.exception.reason, "TOOL_ARG_UNKNOWN")

    def test_arg_type_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("repo_inspect", {"repo": 123}, self.ctx)
        self.assertEqual(ctx.exception.reason, "TOOL_ARG_TYPE")

    def test_destructive_needs_authorization(self):
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("git_commit", {"repo": "x", "message": "m",
                                         "paths": ["a"]}, self.ctx)
        self.assertEqual(ctx.exception.reason, "PERMISSION_DENIED")

    def test_code_search_finds(self):
        out = self.reg.call("code_search", {"repo": str(self.root / "repo"),
                                            "pattern": "needle"}, self.ctx)
        self.assertTrue(out["matches"])
        self.assertIn("a.py", out["matches"][0]["file"])

    def test_repo_inspect(self):
        out = self.reg.call("repo_inspect", {"repo": str(self.root / "repo")},
                            self.ctx)
        self.assertTrue(out["exists"])

    def test_enqueue_roundtrip(self):
        out = self.reg.call("task_enqueue",
                            {"mission_id": "M1", "task_id": "t1",
                             "repo": str(self.root / "repo"),
                             "goal": "do it"}, self.ctx)
        self.assertTrue(out["created"])
        out2 = self.reg.call("task_enqueue",
                             {"mission_id": "M1", "task_id": "t1",
                              "repo": str(self.root / "repo"),
                              "goal": "do it"}, self.ctx)
        self.assertFalse(out2["created"])

    def test_git_commit_explicit_paths(self):
        repo = str(self.root / "repo")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo,
                       check=True)
        ctx = {**self.ctx, "authorize_destructive": True}
        out = self.reg.call("git_commit", {"repo": repo, "message": "first",
                                           "paths": ["a.py"]}, ctx)
        self.assertEqual(out["rc"], 0)
        log = self.reg.call("git_log", {"repo": repo}, self.ctx)
        self.assertIn("first", " ".join(log["commits"]))

    def test_pr_create_never_shell(self):
        with mock.patch("coding_worker_v2.agent.tools._run",
                        side_effect=[(0, "feature"), (0, "[]"),
                                     (0, "https://pr/1")]) as run:
            ctx = {**self.ctx, "authorize_destructive": True,
                   "allow_new_pr": True}
            self.reg.call("pr_create",
                          {"repo": str(self.root / "repo"),
                           "title": "x; rm -rf /"}, ctx)
        argv = run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertIn("--draft", argv)
        self.assertIn("--head", argv)
        self.assertIn("x; rm -rf /", argv)  # single argv element, never parsed

    def test_pr_create_refuses_duplicate_head_branch(self):
        with mock.patch("coding_worker_v2.agent.tools._run",
                        side_effect=[(0, "feature"),
                                     (0, '[{"number":7,"headRefName":"feature"}]')]) as run:
            with self.assertRaises(ToolError) as error:
                tools._pr_create({"repo": str(self.root / "repo"),
                                  "title": "duplicate"})
        self.assertEqual(error.exception.reason, "PR_ALREADY_EXISTS")
        self.assertEqual(run.call_count, 2)

    def test_pr_create_requires_separate_approval(self):
        ctx = {**self.ctx, "authorize_destructive": True}
        with mock.patch("coding_worker_v2.agent.tools._run",
                        return_value=(0, "https://pr/1")) as run:
            with self.assertRaises(ToolError) as error:
                self.reg.call("pr_create",
                              {"repo": str(self.root / "repo"),
                               "title": "new PR"}, ctx)
        self.assertEqual(error.exception.reason, "PR_CREATE_NOT_APPROVED")
        run.assert_not_called()

    def test_git_push_refuses_to_create_a_remote_branch(self):
        ctx = {**self.ctx, "authorize_destructive": True}
        with mock.patch("coding_worker_v2.agent.tools._run",
                        side_effect=[(0, ""), (0, "origin"), (0, "")]) as run:
            with self.assertRaises(ToolError) as error:
                self.reg.call("git_push",
                              {"repo": str(self.root / "repo"),
                               "branch": "new-branch"}, ctx)
        self.assertEqual(error.exception.reason,
                         "BRANCH_CREATE_NOT_APPROVED")
        self.assertEqual(run.call_count, 3)

    def test_ollama_inventory_honest(self):
        out = self.reg.call("ollama_inventory", {}, self.ctx)
        self.assertIn("available", out)

    def test_evidence_captured(self):
        self.reg.call("repo_inspect", {"repo": str(self.root / "repo")},
                      self.ctx)
        events = [e.get("event") for e in self.ctx["evidence"]]
        self.assertIn("tool_called", events)

    def test_photocraft_discovery_missing_tool_and_write_gate(self):
        client = mock.Mock(tools={"session_list": {}, "doc_export": {}},
                           closed=False)
        reg = tools.build_default_registry(client)
        self.assertIn("creative.photocraft.list", reg.names())
        self.assertNotIn("creative.photocraft.inspect", reg.names())
        client.call_tool.return_value = {"content": []}
        reg.call("creative.photocraft.list", {}, self.ctx)
        with self.assertRaises(ToolError):
            reg.call("creative.photocraft.export", {"path": "canary.png"}, self.ctx)
        self.assertEqual(client.call_tool.call_count, 1)
        self.assertFalse(self.ctx["evidence"][-1]["ok"])
        discovery = {row["tool"]: row for row in reg.discover(self.ctx)}
        self.assertFalse(discovery["creative.photocraft.export"]["available"])
        self.assertEqual(reg.command_contract(),
                         tools.build_default_registry(client).command_contract())

    def test_read_only_rejects_writes_even_with_destructive_approval(self):
        with self.assertRaises(ToolError) as caught:
            self.reg.call("task_enqueue", {}, {
                **self.ctx, "read_only": True, "authorize_destructive": True})
        self.assertEqual(caught.exception.reason, "PERMISSION_DENIED")

    def test_artcraft_paths_deny_traversal_absolute_and_symlink(self):
        root = self.root / "output"
        root.mkdir()
        (root / "alias").symlink_to(self.root / "repo", target_is_directory=True)
        for path in ("../escape.png", str(self.root / "outside.png"),
                     "alias/export.png", r"..\escape.png"):
            with self.subTest(path=path), self.assertRaises(ToolError):
                tools._artcraft_path(root, path)
        self.assertEqual(tools._artcraft_path(root, "image.png"), root / "image.png")

    def test_photocraft_export_no_overwrite_and_canvas_cap(self):
        client = tools.PhotoCraftStdio.__new__(tools.PhotoCraftStdio)
        client.tools = {"doc_export": {}, "doc_new": {}}
        client.rpc = mock.Mock(return_value={"content": []})
        (self.root / "output").mkdir()
        (self.root / "output" / "existing.png").write_bytes(b"original")
        with mock.patch.object(tools, "ARTCRAFT_ROOT", self.root):
            for path in ("existing.png", "../escape.png", "image.jpg"):
                with self.subTest(path=path), self.assertRaises(ToolError):
                    client.call_tool("doc_export", {"path": path})
            client.call_tool("doc_export", {"path": "new.png"})
        self.assertEqual(client.rpc.call_count, 1)
        self.assertEqual((self.root / "output" / "existing.png").read_bytes(), b"original")
        for width in (True, -1, 2049):
            with self.subTest(width=width), self.assertRaises(ToolError):
                client.call_tool("doc_new", {"width": width, "height": 64})

    def test_mcp_timeout_fault_and_absent_tool_fail_closed(self):
        client = tools.PhotoCraftStdio.__new__(tools.PhotoCraftStdio)
        client.closed = False
        client.tools = {"session_list": {}}
        client.timeout_s = 0.01
        client._id = 0
        client._buffer = b""
        client._proc = mock.Mock()
        client._selector = mock.Mock()
        client._selector.select.return_value = []
        client._send = mock.Mock()
        client.close = mock.Mock()
        with self.assertRaises(ToolError) as caught:
            client.call_tool("session_list", {})
        self.assertEqual(caught.exception.reason, "MCP_TIMEOUT")
        client.close.assert_called_once()
        with self.assertRaises(ToolError) as caught:
            client.call_tool("doc_export", {})
        self.assertEqual(caught.exception.reason, "MCP_TOOL_ABSENT")
        client.rpc = mock.Mock(return_value={"isError": True})
        with self.assertRaises(ToolError) as caught:
            client.call_tool("session_list", {})
        self.assertEqual(caught.exception.reason, "MCP_TOOL_ERROR")

    def test_mcp_response_cap_and_id_mismatch(self):
        for raw, reason in (
            (b"x" * (tools.MCP_MAX_BYTES + 1), "MCP_RESPONSE_TOO_LARGE"),
            (b'{"jsonrpc":"2.0","id":999,"result":{}}\n',
             "MCP_RESPONSE_ID_MISMATCH"),
        ):
            client = tools.PhotoCraftStdio.__new__(tools.PhotoCraftStdio)
            client.closed, client._id, client._buffer = False, 0, b""
            client.timeout_s = 1
            client._proc, client._selector = mock.Mock(), mock.Mock()
            client._send = mock.Mock()
            client.close = mock.Mock()
            with self.subTest(reason=reason), mock.patch.object(
                    tools.os, "read", return_value=raw):
                with self.assertRaises(ToolError) as caught:
                    client.rpc("tools/list", {})
                self.assertEqual(caught.exception.reason, reason)
                client.close.assert_called_once()

    def test_mcp_blocked_stdin_has_deadline(self):
        client = tools.PhotoCraftStdio.__new__(tools.PhotoCraftStdio)
        client.timeout_s = 0.01
        client._proc = mock.Mock()
        selector = mock.MagicMock()
        selector.__enter__.return_value.select.return_value = []
        with mock.patch.object(tools.selectors, "DefaultSelector", return_value=selector):
            with self.assertRaises(ToolError) as caught:
                client._send(b"request\n")
        self.assertEqual(caught.exception.reason, "MCP_TIMEOUT")

    def test_d1_discovery_unavailable_and_no_network_or_secret_evidence(self):
        ctx = {**self.ctx, "kcc_runtime_token": "fixture-secret-never-log"}
        with mock.patch.object(tools.request, "build_opener") as opener:
            with self.assertRaises(ToolError) as caught:
                self.reg.call("kcc.d1.health", {}, ctx)
        self.assertEqual(caught.exception.reason, "TOOL_UNAVAILABLE")
        opener.assert_not_called()
        self.assertNotIn("fixture-secret-never-log", json.dumps(ctx["evidence"]))

    def test_legacy_kcc_token_never_calls_retired_endpoint(self):
        with mock.patch.object(tools.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ToolError, 'MCP_TOOL_SCOPE_DENIED'):
                tools._kcc_read('task_read', {'task_id': 'existing-task'},
                                {'kcc_runtime_token': 'legacy-not-authority'})
        opener.assert_not_called()

    def test_result_hash_and_failed_retry_trace(self):
        client = mock.Mock(tools={"session_list": {}}, closed=False)
        client.call_tool.side_effect = [{"content": []}, ToolError("MCP_TOOL_ERROR"),
                                       {"content": [{"text": "recovered"}]}]
        reg = tools.build_default_registry(client)
        for success in (True, False, True):
            if success:
                reg.call("creative.photocraft.list", {}, self.ctx)
            else:
                with self.assertRaises(ToolError):
                    reg.call("creative.photocraft.list", {}, self.ctx)
                self.assertNotIn("_creative.photocraft.list_ok", self.ctx)
        events = [e for e in self.ctx["evidence"] if e.get("event") == "tool_called"]
        self.assertEqual([e["ok"] for e in events], [True, False, True])
        self.assertEqual(events[1]["error_class"], "MCP_TOOL_ERROR")
        self.assertIsNone(events[1]["result_sha256"])
        self.assertEqual(len(events[0]["result_sha256"]), 64)


class TestSerenaKMNWContracts(unittest.TestCase):
    """Synthetic session/node contracts; not native Serena or KMNW qualification."""

    def setUp(self):
        from types import SimpleNamespace
        from coding_worker_v2.agent import planner, executor
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "sample.py").write_text("def add(a, b):\n    return a - b\n")
        self.source = "1" * 40
        original = planner.simple_plan("fix add", "G0-G1-MINIMUM-SELLABLE-DEMO",
                                      "pilot-0c3fdc91ed2e2a9011c0cb1d")
        original.tasks[0].allowed_paths = ["sample.py"]
        units = [{"task_id": name, "goal": "fix add", "deps": deps,
                  "allowed_paths": ["sample.py"], "acceptance": ["addition passes"],
                  "input_contract": ["approved source"], "output_contract": ["patch and tests"],
                  "evidence_required": ["independent receipt"]}
                 for name, deps in (("t1", []), ("verify", ["t1"]))]
        self.plan = planner.decompose_plan(original, units, self.source)
        self.store = QueueStore(self.root / "q")
        with mock.patch.object(executor, "current_base_commit", return_value=self.source):
            self.readiness = executor.plan_readiness(self.plan, self.store,
                self.root / "receipts", str(self.root))
        self.session = SimpleNamespace(closed=False, read_only_qualified=True,
            identity_sha256="a" * 64, workspace=str(self.root),
            tools={"find_symbol": {}, "get_symbols_overview": {}})
        self.session.call_tool = mock.Mock(return_value={"result": [{
            "name_path": "add", "relative_path": "sample.py", "line": 1}]})
        self.registry = tools.build_default_registry(serena=self.session)
        self.ctx = {"workspace": str(self.root), "store": self.store}

    def node(self, name="AV15", **over):
        return {"node_id": name, "authenticated": True, "enrollment_verified": True,
                "online": True, "heartbeat_at": 990, "capability_observed_at": 990,
                "capabilities": ["bounded-edit"], "source_sha": self.source,
                "authorized_scope_digests": [t.execution_contract["scope_digest"] for t in self.plan.tasks],
                "lease_available": True, "lease_observed_at": 990,
                "quota": "AVAILABLE", "quota_observed_at": 990, "estimated_cost_usd": 0,
                **{key: "fixture:" + key for key in (
                    "enrollment_evidence_ref", "capability_evidence_ref", "authorization_evidence_ref",
                    "lease_evidence_ref", "ack_contract_ref", "existing_transport_ref")}, **over}

    def routes(self, candidates, **extra):
        from coding_worker_v2.agent.planner import node_route_preview
        return node_route_preview(self.plan, candidates, readiness=self.readiness, now=1000, **extra)

    def test_serena_symbol_forwarding_is_bounded_read_only(self):
        before = (self.root / "sample.py").read_bytes()
        result = self.registry.call("serena.find_symbol",
            {"relative_path": "sample.py", "name_path_pattern": "add"}, self.ctx)
        self.assertEqual(result["result"]["result"][0]["name_path"], "add")
        forwarded = self.session.call_tool.call_args.args[1]
        self.assertFalse(forwarded["include_body"])
        self.assertEqual(forwarded["max_answer_chars"], 8000)
        self.assertEqual((self.root / "sample.py").read_bytes(), before)
        self.assertTrue(result["read_only"])
        self.assertNotIn("serena.execute_shell_command", self.registry.names())
        self.assertNotIn("serena.replace_symbol_body", self.registry.names())

    def test_serena_missing_qualification_scope_and_symlinks_fail_closed(self):
        self.session.read_only_qualified = False
        with self.assertRaisesRegex(ToolError, "QUALIFICATION_REQUIRED"):
            self.registry.call("serena.find_symbol", {"relative_path": "sample.py",
                               "name_path_pattern": "add"}, self.ctx)
        self.session.read_only_qualified = True
        (self.root / "alias.py").symlink_to(self.root / "sample.py")
        for relative in ("../sample.py", ".env", "alias.py"):
            with self.subTest(relative=relative), self.assertRaises(ToolError):
                self.registry.call("serena.symbol_overview", {"relative_path": relative}, self.ctx)
        self.session.call_tool.assert_not_called()

    def test_node_capability_selection_rejects_offline_and_waits_for_dependencies(self):
        from coding_worker_v2.providers import ProviderResources
        ai = ProviderResources(self.store, clock=lambda: 1000).select_advisory([{
            "provider": "qwen-local", "available": True, "observed_at": 990,
            "tested_capabilities": ["bounded-edit"], "capability_evidence_ref": "fixture:AI",
            "security_permissions": ["repo:approved-paths"], "context_limit_tokens": 2048}],
            capability="bounded-edit", context_tokens=100,
            required_permissions=["repo:approved-paths"])
        self.assertEqual(ai["selected_provider"], "qwen-local")
        self.assertFalse(ai["dispatch"])
        result = self.routes([self.node("P02", online=False), self.node()])
        first, child = result["routes"]
        self.assertEqual(first["selected_node"], "AV15")
        self.assertEqual(first["canonical_task_id"], self.plan.plan_id + "-t1")
        self.assertEqual(first["rejected"][0]["reason"], "OFFLINE_OR_STALE")
        self.assertEqual(child["status"], "WAIT_DEPENDENCIES")
        self.assertFalse(first["dispatch"])
        self.assertEqual(first["ack"], "NOT_SENT")
        self.assertEqual(first["lease"], "NOT_CLAIMED")
        resumed = self.routes([self.node()], completed_task_ids=["t1"])
        self.assertEqual(resumed["routes"][0]["status"], "REUSE_VERIFIED_COMPLETION_INPUT")
        self.assertEqual(resumed["routes"][1]["selected_node"], "AV15")

    def test_node_auth_scope_quota_cost_lease_and_ack_are_required(self):
        cases = [(dict(authenticated=False), "ENROLLMENT_OR_AUTH_UNVERIFIED"),
                 (dict(heartbeat_at=1), "OFFLINE_OR_STALE"),
                 (dict(capabilities=[]), "CAPABILITY_UNVERIFIED"),
                 (dict(authorized_scope_digests=[]), "TASK_SCOPE_OR_SOURCE_UNAUTHORIZED"),
                 (dict(lease_available=False), "LEASE_UNAVAILABLE"),
                 (dict(quota="UNKNOWN"), "QUOTA_UNVERIFIED"),
                 (dict(estimated_cost_usd=1), "BUDGET_UNAPPROVED"),
                 (dict(ack_contract_ref=""), "EXISTING_ACK_OR_TRANSPORT_EVIDENCE_MISSING")]
        for change, reason in cases:
            with self.subTest(change=change):
                route = self.routes([self.node(**change)])["routes"][0]
                self.assertIsNone(route["selected_node"])
                self.assertEqual(route["rejected"][0]["reason"], reason)

    def test_node_duplicate_identity_and_missing_memory_rejected(self):
        from coding_worker_v2.agent import planner
        with self.assertRaisesRegex(planner.PlanError, "CANDIDATE_INVALID"):
            self.routes([self.node(), self.node()])
        with self.assertRaisesRegex(planner.PlanError, "MEMORY_REQUIRED"):
            planner.node_route_preview(self.plan, [], readiness={
                "plan_id": self.plan.plan_id, "mission_id": self.plan.mission_id}, now=1000)

    def test_node_routes_use_existing_canonical_namespace_collision_fence(self):
        from coding_worker_v2.agent import planner
        raw = self.plan.to_dict()
        raw["plan_id"] = "p" * 120
        for task in raw["tasks"]:
            task.pop("execution_contract")
        plan = planner.validate_plan_dict(raw)
        with self.assertRaisesRegex(planner.PlanError, "CANONICAL_TASK_COLLISION"):
            planner.node_route_preview(plan, [], readiness={
                "plan_id": plan.plan_id, "mission_id": plan.mission_id,
                "nodes": []}, now=1000)

    def test_existing_queue_duplicate_claim_and_restart_never_dispatch(self):
        from coding_worker_v2.tasks import Task
        from coding_worker_v2.agent import executor
        identity = self.routes([self.node()])["routes"][0]["canonical_task_id"]
        envelope = {"mission_id": self.plan.mission_id, "task_id": identity,
            "owner_node": "AV15", "input": {"repo": str(self.root), "goal": "fix add",
                                               "hints": {"base_commit": self.source}}}
        self.assertEqual(self.store.enqueue(Task.from_dict(envelope)), (identity, True))
        self.assertEqual(self.store.enqueue(Task.from_dict(envelope)), (identity, False))
        current = self.store.claim(identity)
        self.assertIsNone(self.store.claim(identity))
        self.store.checkpoint_task(current, "generated", {"fixture": "no-repeat"})
        lease = self.store.doing / (identity + ".lease.json")
        data = json.loads(lease.read_text()); data["expires_at"] = 0
        lease.write_text(json.dumps(data))
        restarted = QueueStore(self.store.base); restarted.recover()
        resumed = restarted.claim(identity)
        self.assertIsNotNone(resumed)
        self.assertFalse(restarted.heartbeat(identity, current.lease_token))
        self.assertEqual(restarted.load_checkpoint(identity)["data"]["fixture"], "no-repeat")
        with mock.patch.object(executor, "current_base_commit", return_value=self.source), \
                mock.patch.object(executor, "run_task") as execute:
            readiness = executor.plan_readiness(self.plan, restarted, self.root / "receipts", str(self.root))
            from coding_worker_v2.agent.planner import node_route_preview
            result = node_route_preview(self.plan, [self.node()], readiness=readiness, now=1000)
            self.assertEqual(result["routes"][0]["status"], "ATTACH_EXISTING_CHECKPOINT")
            execute.assert_not_called()

    def test_existing_independent_qc_gate_rejects_invalid_patch_binding(self):
        import hashlib
        from coding_worker_v2.agent import qc
        from coding_worker_v2.tasks import Task
        from coding_worker_v2.receipts import write_receipt
        task = Task.from_dict({"mission_id": self.plan.mission_id, "task_id": "fixture-invalid",
            "owner_node": "AV15", "input": {"repo": str(self.root), "goal": "fix add",
                "hints": {"base_commit": self.source, "plan_id": self.plan.plan_id,
                          "plan_digest": "b" * 64, "scope_digest": "fixture-scope",
                          "approved_scope": {"allowed_paths": ["sample.py"]}}}})
        invalid_patch = b"def add(:\n    return 0\n"
        (self.root / "sample.py").write_bytes(invalid_patch)
        task.output = {"patch_digest": hashlib.sha256(invalid_patch).hexdigest(),
                       "changed": ["sample.py"]}
        receipt = write_receipt(self.root / "receipts", task)
        raw = receipt.read_bytes()
        saved = json.loads(raw)
        report = qc.run_qc(task, {"repo": str(self.root), "receipt_path": str(receipt), "executor_id": "AV15",
            "executor_runner_id": "fixture-exec", "independent_verification": {
                "schema": qc.VERIFICATION_SCHEMA, "authenticated": True,
                "signature_sha256": "a" * 64, "verifier_id": "fixture-other",
                "runner_id": "fixture-other-runner", "status": "PASS", "source_sha": self.source,
                "task_id": task.task_id, "mission_id": task.mission_id,
                "plan_id": self.plan.plan_id, "plan_digest": "b" * 64,
                "scope_digest": "fixture-scope", "allowed_paths": ["sample.py"],
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                "receipt_version": saved.get("receipt_version"),
                "lease_token_sha256": saved.get("lease_token_sha256"),
                "evidence_refs": ["fixture:receipt"],
                "patch_digest": "a-different-patch"}}, only=["syntax", "independent_verification"])
        self.assertFalse(report["passed"])
        checks = {check["name"]: check for check in report["checks"]}
        self.assertFalse(checks["syntax"]["passed"])
        self.assertFalse(checks["independent_verification"]["passed"])
        self.assertIn("patch", checks["independent_verification"]["detail"])


if __name__ == "__main__":
    unittest.main()
