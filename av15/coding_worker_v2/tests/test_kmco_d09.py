"""D09 preview contracts; external delivery is deliberately out of scope."""
import unittest
from coding_worker_v2.agent.kmco_d09 import preflight_d09
from coding_worker_v2.agent.tools import ToolError


class D09Tests(unittest.TestCase):
    def setUp(self):
        self.ctx={"mission_id":"m","task_id":"t","account_id":"a",
                  "read_only":True,"egress_owner_authenticated":True}
        self.output={"mission_id":"m","task_id":"t","account_id":"a",
                     "visibility":"PRIVATE_OWNER","evidence_refs":["receipt:abc"],
                     "payload":{"token":"must-never-appear"},"delivery_channel":"CHATGPT"}

    def test_no_send_and_no_payload_leak(self):
        result=preflight_d09(self.output,self.ctx)
        self.assertFalse(result["payload_delivered"])
        self.assertFalse(result["provider_receipt_verified"])
        self.assertNotIn("must-never-appear",str(result))

    def test_cross_tenant_denied(self):
        self.output["account_id"]="other"
        with self.assertRaises(ToolError): preflight_d09(self.output,self.ctx)

    def test_public_visibility_denied(self):
        self.output["visibility"]="PUBLIC"
        with self.assertRaises(ToolError): preflight_d09(self.output,self.ctx)

    def test_owner_auth_required(self):
        self.ctx["egress_owner_authenticated"]=False
        with self.assertRaises(ToolError): preflight_d09(self.output,self.ctx)

    def test_duplicate_evidence_denied(self):
        self.output["evidence_refs"]=["receipt:abc","receipt:abc"]
        with self.assertRaises(ToolError): preflight_d09(self.output,self.ctx)

    def test_unbounded_payload_denied(self):
        self.output["payload"]={"content":"x"*65000}
        with self.assertRaises(ToolError): preflight_d09(self.output,self.ctx)


if __name__=="__main__":
    unittest.main()
