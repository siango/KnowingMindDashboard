"""D07 delegates only to existing P08 scoped memory reader."""
import unittest
from unittest.mock import patch
from coding_worker_v2.agent.kmco_d07 import read_d07
from coding_worker_v2.agent.tools import ToolError


class D07Tests(unittest.TestCase):
    def setUp(self):
        self.ctx={"mission_id":"m","task_id":"t","account_id":"a",
                  "workspace":"/bounded","read_only":True}
        self.args={"goal":"a goal","limit":3}
        self.existing={"circuit":"P08","truth_owner":"EXISTING_KMCW_RECEIPTS",
                       "independent_qc":"NOT_RUN","cases":[]}

    def test_uses_original_memory_reader_without_writes(self):
        with patch("coding_worker_v2.agent.kmco_d07.run_p08",return_value=self.existing) as read:
            result=read_d07(self.args,self.ctx)
        read.assert_called_once_with(self.args,self.ctx)
        self.assertFalse(result["new_memory_saved"])
        self.assertEqual(result["independent_qc"],"NOT_RUN")

    def test_missing_readonly_denied(self):
        self.ctx["read_only"]=False
        with self.assertRaises(ToolError): read_d07(self.args,self.ctx)

    def test_wrong_memory_owner_denied(self):
        self.existing["truth_owner"]="UNKNOWN"
        with patch("coding_worker_v2.agent.kmco_d07.run_p08",return_value=self.existing):
            with self.assertRaises(ToolError): read_d07(self.args,self.ctx)

    def test_extra_args_denied(self):
        self.args["store"]=True
        with self.assertRaises(ToolError): read_d07(self.args,self.ctx)


if __name__=="__main__":
    unittest.main()
