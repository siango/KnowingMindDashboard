"""D11 rejects missing provenance and fabricated live state."""
import unittest
from coding_worker_v2.agent.kmco_d11 import inspect_d11
from coding_worker_v2.agent.tools import ToolError


class D11Tests(unittest.TestCase):
    def setUp(self):
        self.ctx={"mission_id":"m", "task_id":"t", "source_sha":"a"*40,"read_only":True}
        self.req={"mission_id":"m","task_id":"t","source_sha":"a"*40,
            "observations":[{"source":"existing-ledger","evidence_ref":"ledger:123",
                "state":"UNKNOWN","error_code":"", "quota_remaining":None,
                "trace_id":"trace-1"}]}

    def test_preview_does_not_claim_live(self):
        r=inspect_d11(self.req,self.ctx)
        self.assertFalse(r["read_live_ledger"])
        self.assertFalse(r["alert_sent"])
        self.assertFalse(r["recovery_executed"])
        self.assertEqual(r["independent_qc"],"NOT_RUN")

    def test_wrong_sha_denied(self):
        self.req["source_sha"]="b"*40
        with self.assertRaises(ToolError): inspect_d11(self.req,self.ctx)

    def test_wrong_mission_denied(self):
        self.req["mission_id"]="other"
        with self.assertRaises(ToolError): inspect_d11(self.req,self.ctx)

    def test_missing_evidence_denied(self):
        self.req["observations"][0]["evidence_ref"]=""
        with self.assertRaises(ToolError): inspect_d11(self.req,self.ctx)

    def test_negative_quota_denied(self):
        self.req["observations"][0]["quota_remaining"]=-1
        with self.assertRaises(ToolError): inspect_d11(self.req,self.ctx)

    def test_non_readonly_denied(self):
        self.ctx["read_only"]=False
        with self.assertRaises(ToolError): inspect_d11(self.req,self.ctx)


if __name__=="__main__":
    unittest.main()
