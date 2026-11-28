"""P03 source implementation has five genuine bounded control steps, no fake receipts."""
import unittest

from coding_worker_v2.agent.kmco_p03 import (
    P03Blocked, run_p03, attempt_counter, failure_fingerprint,
    progress_delta, repeat_detector,
)
from coding_worker_v2.agent.kmco_runtime import register_kmco_tools
from coding_worker_v2.agent.tools import ToolRegistry, ToolError
from coding_worker_v2.tests.test_kmco_runtime import catalog

MISSION = "G0-G1-MINIMUM-SELLABLE-DEMO"
TASK = "kmco-p03-audit"


def attempts(failures=("FAIL","FAIL","FAIL"), progress=(1,1,1), terminal=False):
    return [
        {"attempt":i+1, "mission_id":MISSION,"task_id":TASK,
         "source_sha256":"a"*64,"failure_class":failure,
         "progress_units":progress[i],"terminal":terminal and i==len(failures)-1}
        for i,failure in enumerate(failures)
    ]


class P03CircuitTests(unittest.TestCase):
    def test_real_five_step_duplicate_stop_and_no_queue_mutation(self):
        inp=attempts()
        self.assertEqual(attempt_counter(inp,mission_id=MISSION,task_id=TASK)["attempts"],3)
        self.assertEqual(len(failure_fingerprint(inp,mission_id=MISSION,task_id=TASK)),3)
        self.assertEqual(progress_delta(inp,mission_id=MISSION,task_id=TASK)["units_delta"],0)
        self.assertEqual(repeat_detector(inp,mission_id=MISSION,task_id=TASK)["consecutive_repeats"],2)
        result=run_p03(inp,mission_id=MISSION,task_id=TASK,max_attempts=6)
        self.assertTrue(result["stop"])
        self.assertEqual(result["reason"],"IDENTICAL_FAILURE_NO_PROGRESS")
        self.assertEqual(result["steps_completed"],[f"P03-M{i:02d}" for i in range(1,6)])
        self.assertFalse(result["queue_mutated"])
        self.assertFalse(result["retry_authorized"])
        self.assertFalse(result["evidence_verified"])

    def test_attempt_budget_and_identity_scope_fail_closed(self):
        inp=attempts(("A","B","C"),(0,2,5))
        self.assertEqual(run_p03(inp,mission_id=MISSION,task_id=TASK,max_attempts=3)["reason"],
                         "RETRY_BUDGET_EXHAUSTED")
        self.assertFalse(run_p03(inp[:2],mission_id=MISSION,task_id=TASK,max_attempts=4)["stop"])
        with self.assertRaisesRegex(P03Blocked,"P03_ATTEMPT_SCOPE_OR_ORDER_INVALID"):
            run_p03([dict(inp[0],task_id="different")],mission_id=MISSION,task_id=TASK)
        with self.assertRaisesRegex(P03Blocked,"P03_ATTEMPT_SCOPE_OR_ORDER_INVALID"):
            run_p03([inp[1]],mission_id=MISSION,task_id=TASK)
        with self.assertRaisesRegex(P03Blocked,"P03_ATTEMPTS_OUT_OF_RANGE"):
            run_p03(inp*5,mission_id=MISSION,task_id=TASK)
        with self.assertRaisesRegex(P03Blocked,"P03_RETRY_BUDGET_INVALID"):
            run_p03(inp,mission_id=MISSION,task_id=TASK,max_attempts=0)

    def test_actual_registry_call_and_mcp_guard(self):
        reg=ToolRegistry()
        register_kmco_tools(reg,catalog())
        inp=attempts(("A","A"),(1,1))
        ctx={"mission_id":MISSION,"task_id":TASK,"evidence":[]}
        r=reg.call("kmco.p03.loop_guard",{"attempts":inp,"max_attempts":4},ctx)
        self.assertEqual(r["status"],"RETRY_ELIGIBILITY_UNVERIFIED")
        with self.assertRaisesRegex(ToolError,"MCP_TOOL_SCOPE_DENIED"):
            reg.call("kmco.p03.loop_guard",{"attempts":inp},ctx,mcp=True)
        with self.assertRaisesRegex(ToolError,"KMCO_P03_TRUSTED_CONTEXT_REQUIRED"):
            reg.call("kmco.p03.loop_guard",{"attempts":inp},{"evidence":[]})


if __name__=="__main__":
    unittest.main()
