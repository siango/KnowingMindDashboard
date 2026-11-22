"""Fail-closed HTTP transport checks, using the real installed registry."""
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request, error

from coding_worker_v2.agent.http_bridge import handler_factory, read_credential
from coding_worker_v2.agent.tools import build_default_registry


class HttpBridgeTests(unittest.TestCase):
    def setUp(self):
        self.secret = b"this-is-a-test-only-private-bearer-token-123"
        self.registry = build_default_registry(circuits=True)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
            handler_factory({"read_only": True}, self.registry, self.secret))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def rpc(self, method, params=None, *, auth=True):
        data = json.dumps({"jsonrpc": "2.0", "id": 5, "method": method,
                           "params": params or {}}).encode()
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.secret.decode()
        req = request.Request(self.url, data=data, headers=headers)
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_anonymous_denied(self):
        status, body = self.rpc("tools/list", auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "AUTH_REQUIRED")

    def test_initialize_and_real_registry(self):
        code, init = self.rpc("initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(code, 200)
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        code, result = self.rpc("tools/list")
        self.assertEqual(code, 200)
        names = {t["name"] for t in result["result"]["tools"]}
        self.assertEqual(names, set(self.registry.names()))
        self.assertIn("kmcp.circuit.run", names)

    def test_write_denied_even_with_bearer(self):
        code, result = self.rpc("tools/call",
                                {"name": "kmcp.circuit.run", "arguments": {}})
        self.assertEqual(code, 200)
        self.assertTrue(result["result"]["isError"])
        self.assertIn("KMCP_HTTP_READ_ONLY_DENIED", result["result"]["content"][0]["text"])

    def test_read_denied_without_issuer(self):
        code, result = self.rpc("tools/call", {"name": "git_log", "arguments": {}})
        self.assertEqual(code, 200)
        self.assertTrue(result["result"]["isError"])

    def test_trusted_read_issuer_uses_original_call_path(self):
        name = next(x["name"] for x in self.registry.command_contract()["commands"]
                    if x["permission"] == "read")
        # Test issuer is injected by the host, not copied from user JSON.
        def issuer(base):
            return {"read_only": True, "account_id": "test-account"}
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
            handler_factory({"read_only": True}, self.registry, self.secret,
                            context_provider=issuer))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"
        with mock.patch("coding_worker_v2.agent.http_bridge._call",
                        return_value={"observed": True}) as called:
            status, body = self.rpc("tools/call", {"name": name, "arguments": {}})
        self.assertEqual(status, 200)
        self.assertFalse(body["result"]["isError"])
        self.assertEqual(called.call_args.args[2]["account_id"], "test-account")
        self.assertTrue(called.call_args.args[2]["read_only"])

    def test_json_cannot_grant_write(self):
        _, result = self.rpc("tools/call", {
            "name": "kmcp.circuit.run",
            "arguments": {"command_auth": {"authenticated": True},
                          "read_only": False}})
        self.assertTrue(result["result"]["isError"])
        self.assertIn("KMCP_HTTP_READ_ONLY_DENIED", result["result"]["content"][0]["text"])

    def test_existing_file_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "existing-token"
            path.write_bytes(self.secret)
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                read_credential(path)
            path.chmod(0o600)
            self.assertEqual(read_credential(path), self.secret)


if __name__ == "__main__":
    unittest.main()
