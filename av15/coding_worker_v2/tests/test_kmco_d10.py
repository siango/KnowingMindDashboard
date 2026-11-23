"""D10 never upgrades supplied snapshot metadata into live proof."""
import unittest
from coding_worker_v2.agent.kmco_d10 import preview_d10
from coding_worker_v2.agent.tools import ToolError


class D10Tests(unittest.TestCase):
    def setUp(self):
        self.ctx={"mission_id":"m","task_id":"t","account_id":"a",
                  "source_sha":"f"*40,"read_only":True}
        self.source={"mission_id":"m","task_id":"t","account_id":"a",
                     "source_sha":"f"*40,"dataset":"ledger","revision":"rev-2",
                     "data_sha256":"a"*64,"evidence_ref":"ledger:2"}
        self.projection={**self.source,"revision":"rev-1",
                         "data_sha256":"b"*64,"evidence_ref":"projection:1"}

    def test_detects_drift_without_write(self):
        out=preview_d10(self.source,self.projection,self.ctx)
        self.assertTrue(out["drift_observed"])
        self.assertFalse(out["projection_written"])
        self.assertFalse(out["source_live_read"])

    def test_match(self):
        out=preview_d10(self.source,self.source.copy(),self.ctx)
        self.assertFalse(out["drift_observed"])

    def test_cross_dataset_denied(self):
        self.projection["dataset"]="other"
        with self.assertRaises(ToolError): preview_d10(self.source,self.projection,self.ctx)

    def test_cross_tenant_denied(self):
        self.projection["account_id"]="other"
        with self.assertRaises(ToolError): preview_d10(self.source,self.projection,self.ctx)

    def test_requires_readonly(self):
        self.ctx["read_only"]=False
        with self.assertRaises(ToolError): preview_d10(self.source,self.projection,self.ctx)

    def test_no_unverifiable_digest(self):
        self.projection["data_sha256"]="not-a-digest"
        with self.assertRaises(ToolError): preview_d10(self.source,self.projection,self.ctx)


if __name__=="__main__":
    unittest.main()
