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

    def test_d1_read_schema_freshness_auth_and_outage(self):
        now = datetime.now(timezone.utc)
        snapshot = {"component": "KCC_D1_RUNTIME_V2", "authenticated": True,
                    "runtime_authority": "CLOUDFLARE_D1",
                    "spreadsheet_runtime_dependency": False,
                    "generated_at": now.isoformat(),
                    "source_health": {"kcc_node_task_queue": {"ok": True}},
                    "tasks": [{"task_id": "existing-task", "lease_id": "existing-lease"}]}
        opener = mock.Mock()
        def response(data):
            result = mock.MagicMock()
            result.__enter__.return_value.read.return_value = json.dumps(data).encode()
            return result
        ctx = {"kcc_runtime_token": "fixture-secret"}
        with mock.patch.object(tools.request, "build_opener", return_value=opener):
            opener.open.return_value = response(snapshot)
            result = tools._kcc_read("task_read", {"task_id": "existing-task"}, ctx)
            self.assertEqual(result["record"]["lease_id"], "existing-lease")
            self.assertNotIn("fixture-secret", json.dumps(result))
            for changed, reason in (
                ({"generated_at": (now - timedelta(seconds=120)).isoformat()},
                 "KCC_SNAPSHOT_STALE"),
                ({"source_health": {"kcc_node_task_queue": {"ok": False}}},
                 "KCC_D1_UNHEALTHY"),
                ({"authenticated": False}, "KCC_AUTHORITY_INVALID"),
            ):
                opener.open.return_value = response({**snapshot, **changed})
                with self.subTest(reason=reason), self.assertRaises(ToolError) as caught:
                    tools._kcc_read("health", {}, ctx)
                self.assertEqual(caught.exception.reason, reason)
            for fault, reason in (
                (error.HTTPError(tools.KCC_RUNTIME_ENDPOINT, 401, "", {}, None),
                 "KCC_AUTH_DENIED"),
                (error.HTTPError(tools.KCC_RUNTIME_ENDPOINT, 302, "", {}, None),
                 "KCC_HTTP_UNAVAILABLE"),
                (error.URLError("offline"), "KCC_READ_UNAVAILABLE"),
            ):
                opener.open.side_effect = fault
                with self.subTest(reason=reason), self.assertRaises(ToolError) as caught:
                    tools._kcc_read("health", {}, ctx)
                self.assertEqual(caught.exception.reason, reason)
        with self.assertRaises(ToolError):
            tools._kcc_read("health", {}, {})

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


if __name__ == "__main__":
    unittest.main()
