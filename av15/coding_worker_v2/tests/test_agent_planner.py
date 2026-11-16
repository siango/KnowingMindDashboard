"""V3 planner tests: schema validation, ordering, preview, fallback."""
import sys
import os
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import planner  # noqa: E402
from coding_worker_v2.agent.planner import (PlanError, order_tasks,  # noqa: E402
                                            parse_plan, preview, simple_plan,
                                            validate_plan_dict)


class TestTaskIOEvaluation(unittest.TestCase):
    """New IO contracts only; original GO-G1 production state is never run."""

    def setUp(self):
        import hashlib
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        isolated_state = mock.patch.dict(os.environ, {'KMW2_STATE_DIR': str(self.root / 'state')})
        isolated_state.start()
        self.addCleanup(isolated_state.stop)
        self.before = b"def add(a,b): return a-b\n"
        self.after = b"def add(a,b): return a+b\n"
        (self.root / "calc.py").write_bytes(self.before)
        original = validate_plan_dict(good_dict(
            mission_id="G0-G1-MINIMUM-SELLABLE-DEMO",
            plan_id="pilot-0c3fdc91ed2e2a9011c0cb1d"))
        self.unit = {"task_id": "t1", "goal": "fix add", "deps": [],
            "allowed_paths": ["calc.py"], "acceptance": ["addition passes"],
            "input_contract": ["pinned input"], "output_contract": ["typed output and pinned artifact"],
            "evidence_required": ["passing test evidence"], "io_checks": {
                "input_sha256": {"calc.py": hashlib.sha256(self.before).hexdigest()},
                "artifact_sha256": {"calc.py": hashlib.sha256(self.after).hexdigest()},
                "output_fields": {"changed": "list", "patch_digest": "sha256"},
                "required_events": ["tests_run"]}}
        self.plan = planner.decompose_plan(original, [self.unit], "1" * 40)
        self.output = {"changed": ["calc.py"], "patch_digest": "a" * 64}
        self.evidence = [{"event": "tests_run", "tests": {"ran": True, "rc": 0}}]

    def test_explicit_input_output_and_test_evidence_pass_without_qc_promotion(self):
        from coding_worker_v2.agent.qc import evaluate_task_io
        planned = self.plan.tasks[0]
        self.assertTrue(evaluate_task_io(planned, self.root, phase="input")["passed"])
        (self.root / "calc.py").write_bytes(self.after)
        result = evaluate_task_io(planned, self.root, phase="output",
                                  output=self.output, evidence=self.evidence)
        self.assertTrue(result["passed"])
        self.assertFalse(result["independent_verified"])

    def test_invalid_output_artifact_or_missing_test_evidence_rejected(self):
        from coding_worker_v2.agent.qc import evaluate_task_io
        (self.root / "calc.py").write_bytes(self.after)
        for output, evidence in (({**self.output, "patch_digest": "invalid"}, self.evidence),
                                 ({**self.output, "changed": ["../outside.py"]}, self.evidence),
                                 ({**self.output, "changed": []}, self.evidence),
                                 (self.output, []),
                                 (self.output, [{"event": "tests_run", "tests": {"ran": True, "rc": 1}}])):
            with self.subTest(output=output, evidence=evidence):
                self.assertFalse(evaluate_task_io(self.plan.tasks[0], self.root, phase="output",
                    output=output, evidence=evidence)["passed"])
        (self.root / "calc.py").write_bytes(b"unexpected artifact")
        self.assertFalse(evaluate_task_io(self.plan.tasks[0], self.root, phase="output",
            output=self.output, evidence=self.evidence)["passed"])

    def test_input_failure_prevents_admit_enqueue_and_dispatch(self):
        from coding_worker_v2.agent import executor
        from coding_worker_v2.config import WorkerConfig
        from coding_worker_v2.queue_store import QueueStore
        store = QueueStore(self.root / "q")
        (self.root / "calc.py").write_bytes(b"changed input")
        with mock.patch.object(executor, "current_base_commit", return_value="1" * 40), \
                mock.patch.object(executor, "run_mission_audit") as audit, \
                mock.patch.object(executor, "run_task") as dispatch:
            result = executor.execute_plan(self.plan, WorkerConfig(), store,
                self.root / "receipts", self.root / "state", str(self.root))
            self.assertEqual(result["next_action"], "TASK_INPUT_CONTRACT_REJECTED")
            self.assertEqual(store.health()["inbox"], 0)
            audit.assert_not_called(); dispatch.assert_not_called()

    def test_invalid_output_is_archived_once_and_retry_does_not_execute(self):
        from types import SimpleNamespace
        from coding_worker_v2.agent import executor, memory
        from coding_worker_v2.agent.auditor import context_digest
        from coding_worker_v2.config import WorkerConfig
        from coding_worker_v2.queue_store import QueueStore
        from coding_worker_v2.receipts import read_receipt
        store = QueueStore(self.root / "q")
        registry = SimpleNamespace(create=lambda *_: {"workspace_path": str(self.root), "branch": "fixture"})
        def audit(stage, context):
            return {"gate_passed": True, "context_digest": context_digest(stage, context)}
        def invalid(task, *args, **kwargs):
            (self.root / "calc.py").write_bytes(b"invalid output")
            task.status = "DONE"; task.output = dict(self.output)
            task.evidence.extend(self.evidence)
            return task
        with mock.patch.object(executor, "current_base_commit", return_value="1" * 40), \
                mock.patch.object(executor, "WorktreeRegistry", return_value=registry), \
                mock.patch.object(executor, "qwen_chain", return_value=[]), \
                mock.patch.object(executor, "run_task", side_effect=invalid) as dispatch, \
                mock.patch.object(executor, "wait_signed_proof") as verifier:
            first = executor.execute_plan(self.plan, WorkerConfig(), store,
                self.root / "receipts", self.root / "state", str(self.root), audit_fn=audit)
            self.assertEqual(first["status"], "FAILED")
            receipt = read_receipt(Path(first["tasks"][0]["receipt"]))
            self.assertEqual(receipt["next_action"], "TASK_OUTPUT_CONTRACT_REJECTED")
            self.assertEqual(memory.terminal_conflicts(store, receipt), [])
            second = executor.execute_plan(self.plan, WorkerConfig(), store,
                self.root / "receipts", self.root / "state", str(self.root), audit_fn=audit)
            self.assertEqual(second["status"], "BLOCKED")
            self.assertEqual(dispatch.call_count, 1)  # A synthetic invalid-output stub only.
            verifier.assert_not_called()
            self.assertEqual(store.health()["done"], 0)
            self.assertEqual(store.health()["failed"], 1)

    def test_no_prose_inference_scope_expansion_or_symlink_read(self):
        from coding_worker_v2.agent.qc import evaluate_task_io
        legacy = validate_plan_dict(good_dict()).tasks[0]
        self.assertEqual(evaluate_task_io(legacy, self.root, phase="input")["status"], "NOT_CONFIGURED")
        bad = dict(self.unit["io_checks"])
        bad["input_sha256"] = {"unapproved.py": "a" * 64}
        with self.assertRaisesRegex(PlanError, "SCOPE_OR_HASH_INVALID"):
            planner.validate_io_checks(bad, ["calc.py"])
        (self.root / "original.py").write_bytes(self.before)
        (self.root / "calc.py").unlink()
        (self.root / "calc.py").symlink_to(self.root / "original.py")
        self.assertFalse(evaluate_task_io(self.plan.tasks[0], self.root, phase="input")["passed"])


class TestBoundedOrchestration(unittest.TestCase):
    """GO-G1 identity in isolated fixtures; no production admission/execution."""

    def setUp(self):
        self.original = validate_plan_dict(good_dict(
            mission_id="G0-G1-MINIMUM-SELLABLE-DEMO",
            plan_id="pilot-0c3fdc91ed2e2a9011c0cb1d"))
        self.source = "1" * 40  # Fixture source, never a production observation.
        self.units = [
            {"task_id": name, "goal": goal, "deps": deps,
             "allowed_paths": ["calc.py"], "acceptance": [accept],
             "input_contract": ["approved calc.py at fixture source"],
             "output_contract": [output],
             "evidence_required": ["source hash", "test result", "independent QC"]}
            for name, goal, deps, accept, output in (
                ("inspect", "Inspect addition defect", [], "cause supported by source", "cause evidence"),
                ("t1", "Fix add()", ["inspect"], "2+3==5", "bounded patch"),
                ("verify", "Verify addition", ["t1"], "scope tests and separate QC pass", "verification evidence"))]

    def plan(self):
        return planner.decompose_plan(self.original, self.units, self.source)

    def test_deterministic_dag_preserves_original_identity_and_bytes(self):
        before = json.dumps(self.original.to_dict(), sort_keys=True)
        first, second = self.plan(), self.plan()
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.mission_id, self.original.mission_id)
        self.assertEqual(first.plan_id, self.original.plan_id)
        self.assertEqual([t.task_id for t in order_tasks(first)], ["inspect", "t1", "verify"])
        self.assertEqual(json.dumps(self.original.to_dict(), sort_keys=True), before)
        self.assertNotIn("execution_contract", self.original.tasks[0].to_dict())

    def test_scope_expansion_cycle_and_tampering_are_rejected(self):
        expanded = [{**u, "allowed_paths": ["unapproved.py"]} for u in self.units]
        with self.assertRaisesRegex(PlanError, "SCOPE_EXPANSION"):
            planner.decompose_plan(self.original, expanded, self.source)
        cycle = [dict(u) for u in self.units]
        cycle[0]["deps"] = ["verify"]
        with self.assertRaisesRegex(PlanError, "DEP_CYCLE"):
            planner.decompose_plan(self.original, cycle, self.source)
        data = self.plan().to_dict()
        data["tasks"][0]["acceptance"] = ["invented pass"]
        with self.assertRaisesRegex(PlanError, "EXECUTION_CONTRACT_INVALID"):
            validate_plan_dict(data)

    def test_memory_precedes_admit_and_retained_block_skips_audit_on_restart(self):
        from coding_worker_v2.agent import executor
        from coding_worker_v2.config import WorkerConfig
        from coding_worker_v2.queue_store import QueueStore
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "calc.py").write_text("def add(a,b): return a-b\n")
            store = QueueStore(root / "q")
            plan = self.plan()
            calls = []
            def history(*args, **kwargs):
                calls.append("memory")
                return []
            def audit(stage, context):
                calls.append(stage)
                return {"gate_passed": False, "reason": "FIXTURE_AUTHORITY_MISSING"}
            with mock.patch.object(executor, "current_base_commit", return_value=self.source), \
                    mock.patch.object(executor, "retrieve_work", side_effect=history), \
                    mock.patch.object(executor, "run_task") as dispatch:
                first = executor.execute_plan(plan, WorkerConfig(), store, root / "receipts",
                    root / "state", str(root), audit_fn=audit)
                self.assertEqual(first["status"], "BLOCKED")
                self.assertEqual(calls[:3], ["memory"] * 3)
                self.assertEqual(calls[3:], ["ADMIT"])
                store.save_checkpoint(f".pilot-{plan.plan_id}", "pilot_run_readback",
                                      {"report": first})
                before = {p.name: p.read_bytes() for p in store.events.glob("*.jsonl")}
                restarted = QueueStore(store.base)
                calls.clear()
                second = executor.execute_plan(plan, WorkerConfig(), restarted, root / "receipts",
                    root / "state", str(root), audit_fn=audit)
                self.assertEqual(second["next_action"], "RETAIN_BLOCKED_CHECKPOINT")
                self.assertEqual(calls, ["memory"] * 3)
                self.assertEqual({p.name: p.read_bytes() for p in store.events.glob("*.jsonl")}, before)
                self.assertFalse(second["readiness"]["concurrency_authorized"])
                self.assertEqual(restarted.health()["inbox"], 0)
                dispatch.assert_not_called()
                legacy = self.original
                legacy_report = executor.execute_plan(legacy, WorkerConfig(), restarted,
                    root / "receipts", root / "state", str(root), audit_fn=audit)
                self.assertEqual(legacy_report["next_action"], "RETAIN_BLOCKED_CHECKPOINT")

    def test_queue_dedup_and_expired_lease_restart_are_existing_contracts(self):
        from coding_worker_v2.queue_store import QueueStore
        from coding_worker_v2.tasks import Task
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "q")
            task = Task.from_dict({"mission_id": self.original.mission_id,
                "task_id": self.original.plan_id + "-t1", "owner_node": "AV15",
                "input": {"repo": str(root), "goal": "fixture only"}})
            self.assertEqual(store.enqueue(task), (task.task_id, True))
            self.assertEqual(store.enqueue(Task.from_dict(task.to_dict())), (task.task_id, False))
            claimed = store.claim(task.task_id)
            self.assertIsNotNone(claimed)
            store.checkpoint_task(claimed, "generated", {"fixture": "retained"})
            lease = store.doing / f"{task.task_id}.lease.json"
            data = json.loads(lease.read_text()); data["expires_at"] = 0
            lease.write_text(json.dumps(data))
            restarted = QueueStore(store.base)
            restarted.recover()
            current = restarted.claim(task.task_id)
            self.assertIsNotNone(current)
            self.assertNotEqual(current.lease_token, claimed.lease_token)
            self.assertFalse(restarted.heartbeat(task.task_id, claimed.lease_token))
            self.assertEqual(restarted.load_checkpoint(task.task_id)["data"]["fixture"], "retained")
            self.assertIsNone(restarted.claim(task.task_id))

    def test_failed_qc_proposes_bounded_child_without_new_mission(self):
        plan = self.plan()
        repair = planner.bounded_repair_task(plan, "t1", ["unit_tests", "scope"], 1)
        self.assertEqual(repair["mission_id"], plan.mission_id)
        self.assertEqual(repair["plan_id"], plan.plan_id)
        self.assertEqual(repair["task"]["allowed_paths"], ["calc.py"])
        self.assertEqual(repair["task"]["max_repairs"], 0)
        self.assertEqual(repair["task"]["task_id"], "t1.repair-1")
        self.assertEqual(repair["status"], "PROPOSED_NOT_ENQUEUED")
        self.assertTrue(repair["admit_required"])
        with self.assertRaisesRegex(PlanError, "BUDGET_MISSING"):
            planner.bounded_repair_task(plan, "t1", ["unit_tests"], 99)


def good_dict(**over):
    data = {"schema": "KM_TASK_PLAN_V1", "mission_id": "M1", "plan_id": "p1",
            "owner_node": "AV15", "source_command": "fix it",
            "tasks": [{"task_id": "t1", "goal": "fix add()",
                       "deps": [], "priority": 1, "complexity": "S",
                       "acceptance": ["2+3==5"],
                       "allowed_paths": ["calc.py"], "tools": [],
                       "model": "qwen-local", "max_repairs": 2}]}
    data.update(over)
    return data


class TestPlanValidation(unittest.TestCase):
    def test_learned_method_selection_uses_evidence_and_context_without_authority(self):
        import copy
        lesson={'classification':'VERIFIED','context_comparison':{'state':'MATCH'},
            'provenance':{'original_ref':'isolated:receipt','sha256':'a'*64},
            'recommendation':{'provider':'qwen-local'},'observed_cause':[{'ref':'isolated:cause'}]}
        reuse=planner.select_method({'lessons':[lesson]})
        self.assertEqual(reuse['method'],'REUSE');self.assertFalse(reuse['execution_authority'])
        observed=copy.deepcopy(lesson);observed.update(classification='OBSERVED',
            recommendation=None,method_hint='REENGINEER')
        changed=planner.select_method({'lessons':[observed]})
        self.assertEqual(changed['method'],'REENGINEER')
        self.assertEqual(changed['action'],'INSPECT_EXISTING_SCOPE')
        observed['method_hint']='ADAPT'
        self.assertEqual(planner.select_method({'lessons':[observed]})['method'],'ADAPT')
        observed['context_comparison']={'state':'DIFFERENT','different_facets':['environment']}
        self.assertEqual(planner.select_method({'lessons':[observed]})['method'],'ADAPT')
        observed['context_comparison']={'state':'MATCH'}
        observed['method_hint']=[]
        self.assertEqual(planner.select_method({'lessons':[observed]})['method'],'NO_GROUNDED_METHOD')
        observed['provenance']={}
        self.assertEqual(planner.select_method({'lessons':[observed]})['method'],'NO_GROUNDED_METHOD')
        self.assertEqual(planner.select_method({'lessons':[]})['action'],'KEEP_EXISTING_FLOW')


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

    def test_selected_tool_is_rejected_when_executor_does_not_dispatch(self):
        data = good_dict()
        data["tasks"][0]["tools"] = ["repo_inspect"]
        with self.assertRaises(PlanError) as ctx:
            validate_plan_dict(data, ["repo_inspect"])
        self.assertEqual(ctx.exception.reason, "PLAN_TOOL_UNSUPPORTED")

    def test_selected_model_is_rejected_when_executor_does_not_dispatch(self):
        data = good_dict()
        data["tasks"][0]["tools"] = []
        data["tasks"][0]["model"] = "muse-cli"
        with self.assertRaises(PlanError) as ctx:
            validate_plan_dict(data)
        self.assertEqual(ctx.exception.reason, "PLAN_MODEL_UNSUPPORTED")

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

    def test_unsafe_approved_path_rejected(self):
        data = good_dict()
        data["tasks"][0]["allowed_paths"] = ["../outside.py"]
        with self.assertRaises(PlanError) as ctx:
            validate_plan_dict(data)
        self.assertEqual(ctx.exception.reason, "PLAN_SCOPE_PATHS_INVALID")


class TestPlanOrder(unittest.TestCase):
    def test_topo_order(self):
        data = good_dict(tasks=[
            {"task_id": "a", "goal": "first", "acceptance": ["ok"],
             "allowed_paths": ["a.py"]},
            {"task_id": "b", "goal": "second", "deps": ["a"],
             "acceptance": ["ok"], "allowed_paths": ["b.py"]}])
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
