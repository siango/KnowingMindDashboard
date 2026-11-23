"""D06 does not invent sourced Q&A or channel delivery."""
import unittest
from unittest.mock import patch
from coding_worker_v2.agent import ingress
from coding_worker_v2.agent.kmco_d06 import preview_d06
from coding_worker_v2.agent.tools import ToolError


class D06Tests(unittest.TestCase):
    def setUp(self):
        self.ctx={"mission_id":"m","task_id":"t","account_id":"a",
                  "command_auth":{"mission_id":"m","account_id":"a","task_ids":["t"]}}
        self.req={"mission_id":"m","task_id":"t","account_id":"a",
                  "question":"What is the source?","source_refs":[]}

    def test_authenticated_preview_is_not_answer(self):
        with patch.object(ingress,"qualified",return_value=True):
            result=preview_d06(self.req,self.ctx)
        self.assertFalse(result["canon_retrieved"])
        self.assertFalse(result["model_invoked"])
        self.assertFalse(result["answer_delivered"])
        self.assertFalse(result["source_refs_verified"])

    def test_unqualified_denied(self):
        with patch.object(ingress,"qualified",return_value=False):
            with self.assertRaises(ToolError): preview_d06(self.req,self.ctx)

    def test_cross_tenant_denied(self):
        self.req["account_id"]="other"
        with patch.object(ingress,"qualified",return_value=True):
            with self.assertRaises(ToolError): preview_d06(self.req,self.ctx)

    def test_unbound_task_denied(self):
        self.req["task_id"]="other"
        with patch.object(ingress,"qualified",return_value=True):
            with self.assertRaises(ToolError): preview_d06(self.req,self.ctx)

    def test_unbounded_question_denied(self):
        self.req["question"]="x"*3501
        with patch.object(ingress,"qualified",return_value=True):
            with self.assertRaises(ToolError): preview_d06(self.req,self.ctx)


if __name__=="__main__":
    unittest.main()
