"""Reuse real KMCW planner contract for five P04 stages."""
import unittest

from coding_worker_v2.agent.kmco_p04 import (
    P04Blocked, plan_validator, goal_parser, scope_binder, task_atomizer, dependency_dag,
)
from coding_worker_v2.agent.kmco_runtime import register_kmco_tools
from coding_worker_v2.agent.tools import ToolRegistry, ToolError
from coding_worker_v2.tests.test_kmco_runtime import catalog

PLAN = {
    "schema":"KM_TASK_PLAN_V1","mission_id":"KMCP-V5-PRODUCTION",
    "plan_id":"kmco-p04","owner_node":"AV15",
    "source_command":"review existing source only",
    "tasks":[
        {"task_id":"inspect","goal":"inspect existing path",
         "deps":[],"acceptance":["source SHA"],"allowed_paths":["av15"]},
        {"task_id":"check","goal":"check existing source",
         "deps":["inspect"],"acceptance":["QC evidence"],"allowed_paths":["av15"]},
    ]
}


class P04Tests(unittest.TestCase):
    def test_actual_planner_five_steps(self):
        p=goal_parser(PLAN,"KMCP-V5-PRODUCTION")
        self.assertEqual(list(scope_binder(p,"AV15")),["inspect","check"])
        self.assertEqual(task_atomizer(p),["inspect","check"])
        self.assertEqual(dependency_dag(p),["inspect","check"])
        result=plan_validator(PLAN,"KMCP-V5-PRODUCTION","AV15")
        self.assertEqual(result["steps_completed"],[f"P04-M{i:02d}" for i in range(1,6)])
        self.assertEqual(result["ordered_task_ids"],["inspect","check"])
        self.assertFalse(result["admitted"])
        self.assertFalse(result["dispatched"])
        self.assertFalse(result["authority_granted"])

    def test_real_existing_validator_rejects_bad_scope_and_cycle(self):
        data={**PLAN,"tasks":[dict(PLAN["tasks"][0],allowed_paths=[]),PLAN["tasks"][1]]}
        with self.assertRaisesRegex(P04Blocked,"P04_TASK_SCOPE_REQUIRED"):
            plan_validator(data,"KMCP-V5-PRODUCTION","AV15")
        data={**PLAN,"tasks":[dict(PLAN["tasks"][0],deps=["check"]),PLAN["tasks"][1]]}
        with self.assertRaisesRegex(P04Blocked,"PLAN_DEP_CYCLE"):
            plan_validator(data,"KMCP-V5-PRODUCTION","AV15")
        with self.assertRaisesRegex(P04Blocked,"P04_MISSION_OR_GOAL_MISMATCH"):
            plan_validator(PLAN,"another","AV15")

    def test_actual_registry_and_unauthorized_mcp_deny(self):
        reg=ToolRegistry()
        register_kmco_tools(reg,catalog())
        ctx={"mission_id":"KMCP-V5-PRODUCTION","owner_node":"AV15","evidence":[]}
        r=reg.call("kmco.p04.plan",{"plan":PLAN},ctx)
        self.assertEqual(r["status"],"VALIDATED_PLAN_PREVIEW_ONLY")
        with self.assertRaisesRegex(ToolError,"MCP_TOOL_SCOPE_DENIED"):
            reg.call("kmco.p04.plan",{"plan":PLAN},ctx,mcp=True)
        with self.assertRaisesRegex(ToolError,"KMCO_P04_ISSUER_REQUIRED"):
            reg.call("kmco.p04.plan",{"plan":PLAN},{"evidence":[]})


if __name__=="__main__":
    unittest.main()
