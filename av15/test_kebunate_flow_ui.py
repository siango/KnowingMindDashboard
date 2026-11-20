"""KEBUNATE Flow Studio route contract tests (no invented AV15 status)."""
import asyncio
import os
import unittest
from unittest.mock import patch

from av15.kebunate_flow_ui import handle, snapshot
from av15.mcpv5_secure_http_readback import AccessGate


class FlowTests(unittest.TestCase):
    def request(self, path, method="GET"):
        messages=[]
        async def send(message):
            messages.append(message)
        async def receive():
            return {"type":"http.request","body":b"","more_body":False}
        asyncio.run(handle({"type":"http","path":path,"method":method},receive,send))
        return messages

    def test_flow_html_readable_and_no_fake_live(self):
        output=self.request("/flow")
        self.assertEqual(output[0]["status"],200)
        data=output[1]["body"].decode("utf-8")
        self.assertIn("NO FAKE LIVE",data)
        self.assertIn("/flow/api",data)
        self.assertIn("NOT VERIFIED",data)

    def test_flow_api_reports_unavailable_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            d=snapshot()
        self.assertEqual(d["db"]["state"],"UNAVAILABLE")
        self.assertFalse(d["live_data"])
        self.assertFalse(d["acceptance"]["independent_receipt"])
        self.assertEqual(d["acceptance"]["whole_six_systems"],"NOT_VERIFIED")

    def test_flow_rejects_mutating_method(self):
        self.assertEqual(self.request("/flow/api","POST")[0]["status"],405)

    def test_flow_security_headers(self):
        headers=dict(self.request("/flow")[0]["headers"])
        self.assertEqual(headers[b"x-frame-options"],b"DENY")
        self.assertIn(b"no-store",headers[b"cache-control"])

    def test_access_gate_rejects_anonymous_flow(self):
        async def unreachable(scope,receive,send):
            raise AssertionError("UPSTREAM_CALLED")
        gate=AccessGate(unreachable)
        messages=[]
        async def send(m):messages.append(m)
        async def receive():return {"type":"http.request","body":b"","more_body":False}
        asyncio.run(gate({"type":"http","path":"/flow/api","headers":[]},receive,send))
        self.assertEqual(messages[0]["status"],401)


if __name__=="__main__":
    unittest.main()
