"""V3 planner tests: schema validation, ordering, preview, fallback."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import planner  # noqa: E402
from coding_worker_v2.agent.planner import (PlanError, order_tasks,  # noqa: E402
                                            parse_plan, preview, simple_plan,
                                            validate_plan_dict)


def good_dict(**over):
    data = {"schema": "KM_TASK_PLAN_V1", "mission_id": "M1", "plan_id": "p1",
            "owner_node": "AV15", "source_command": "fix it",
            "tasks": [{"task_id": "t1", "goal": "fix add()",
                       "deps": [], "priority": 1, "complexity": "S",
                       "acceptance": ["2+3==5"], "tools": ["repo_inspect"],
                       "model": "qwen-local", "max_repairs": 2}]}
    data.update(over)
    return data


class TestPlanValidation(unittest.TestCase):
    def test_valid(self):
        plan = validate_plan_dict(good_dict(), ["repo_inspect"])
        self.assertEqual(plan.tasks[0].task_id, "t1")

    def test_schema_mismatch(self):
        with self.assertRaises(PlanError):
            validate_plan_dict(good_dict(schema="NOPE"))

    def test_empty_tasks_rejected(self):
        with self.assertRaises(PlanError):
            validate_plan_dict(good_dict(tasks=[]))

    def test_too_many_tasks_rejected(self):
        tasks = [{"task_id": f"t{i}", "goal": "g", "acceptance": ["a"]}
                 for i in range(25)]
        with self.assertRaises(PlanError):
            validate_plan_dict(good_dict(tasks=tasks))

    def test_unknown_tool_rejected_strict(self):
        data = good_dict()
        data["tasks"][0]["tools"] = ["nope_tool"]
        with self.assertRaises(PlanError) as ctx:
            validate_plan_dict(data, ["repo_inspect"])
        self.assertEqual(ctx.exception.reason, "PLAN_TOOL_UNKNOWN")

    def test_unknown_dep_rejected(self):
        data = good_dict()
        data["tasks"][0]["deps"] = ["ghost"]
        with self.assertRaises(PlanError):
            validate_plan_dict(data)

    def test_missing_acceptance_rejected(self):
        data = good_dict()
        data["tasks"][0]["acceptance"] = []
        with self.assertRaises(PlanError):
            validate_plan_dict(data)

    def test_bad_priority_rejected(self):
        data = good_dict()
        data["tasks"][0]["priority"] = 9
        with self.assertRaises(PlanError):
            validate_plan_dict(data)


class TestPlanOrder(unittest.TestCase):
    def test_topo_order(self):
        data = good_dict(tasks=[
            {"task_id": "a", "goal": "first", "acceptance": ["ok"]},
            {"task_id": "b", "goal": "second", "deps": ["a"],
             "acceptance": ["ok"]}])
        plan = validate_plan_dict(data)
        self.assertEqual([t.task_id for t in order_tasks(plan)], ["a", "b"])

    def test_cycle_rejected(self):
        data = good_dict(tasks=[
            {"task_id": "a", "goal": "x", "deps": ["b"], "acceptance": ["ok"]},
            {"task_id": "b", "goal": "y", "deps": ["a"], "acceptance": ["ok"]}])
        plan = validate_plan_dict(data)
        with self.assertRaises(PlanError) as ctx:
            order_tasks(plan)
        self.assertEqual(ctx.exception.reason, "PLAN_DEP_CYCLE")

    def test_preview_and_simple(self):
        plan = simple_plan("ตรวจสอบโค้ดและแก้บั๊ก", "M1", "p9")
        self.assertEqual(plan.engine, "deterministic-fallback")
        text = preview(plan)
        self.assertIn("p9", text)
        self.assertIn("t1", text)

    def test_simple_empty_rejected(self):
        with self.assertRaises(PlanError):
            simple_plan("  ", "M1", "p1")

    def test_parse_fenced(self):
        import json
        text = "```json\n" + json.dumps(good_dict()) + "\n```"
        self.assertEqual(parse_plan(text)["plan_id"], "p1")

    def test_parse_garbage_rejected(self):
        with self.assertRaises(PlanError):
            parse_plan("just prose, no json")


if __name__ == "__main__":
    unittest.main()
