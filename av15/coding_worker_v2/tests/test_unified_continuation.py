"""GO-G1 isolated continuation fixtures. No live providers, network or authority."""
import copy
import fcntl
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from coding_worker_v2.agent import conversation, executor, memory, pilot, planner, qc, tools
from coding_worker_v2.agent.verification import VerificationError
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.receipts import write_receipt
from coding_worker_v2.tasks import Task, TaskInput
from coding_worker_v2.tests.test_agent_executor import fixture_repo


class UnifiedContinuationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = fixture_repo(self.root)
        self.env = mock.patch.dict(os.environ, {"KMW2_OWNER_NODE": "AV15",
                                               "KMW2_STATE_DIR": str(self.root / 'runtime-state')})
        self.env.start()
        self.addCleanup(self.env.stop)
        config = {"schema": pilot.SCHEMA, "account_id": "fixture-account", "worker_id": "AV15",
                  "workspace": self.repo, "runtime_base": str(self.root / "runtime"),
                  "allowed_paths": ["calc.py"], "context_paths": ["calc.py", "test_calc.py"],
                  "test_command": "python3 -m unittest", "provider": "qwen-local"}
        p = self.root / "config.json"
        p.write_text(json.dumps(config))
        self.ctx = pilot.load_context(p)
        self.sha = memory.current_base_commit(self.repo)
        submitted = pilot.submit({"request_id": "existing-go-g1", "goal": "Repair calculator",
                                 "allowed_paths": ["calc.py"], "acceptance": ["addition passes"]}, self.ctx)
        self.plan_id = submitted["plan_id"]
        original, self.binding = pilot._plan(self.plan_id, self.ctx)
        self.units = [{"task_id": name, "goal": goal, "deps": deps, "allowed_paths": ["calc.py"],
                       "acceptance": ["addition passes"], "input_contract": ["pinned calculator"],
                       "output_contract": ["valid calculator"], "evidence_required": ["tests and independent QC"]}
                      for name, goal, deps in (("t1", "Repair calculator", []),
                                               ("t2", "Inspect calculator", []),
                                               ("verify", "Verify integration", ["t1", "t2"]))]
        self.plan = planner.decompose_plan(original, self.units, self.sha)
        self.save_plan()
        # Fail if a new integration accidentally calls a real provider.
        self.generate = mock.patch("coding_worker_v2.providers.QwenProvider.generate",
                                   side_effect=AssertionError("LIVE_INFERENCE_FORBIDDEN")).start()
        self.addCleanup(mock.patch.stopall)

    def test_reuse_conversation_and_node_evidence_keep_registry_contract(self):
        registry=tools.build_default_registry();self.ctx['tool_registry']=registry
        contract=registry.command_contract()
        with mock.patch.object(tools,'build_default_registry',
                side_effect=AssertionError('must retain original registry')):
            response=self.chat('DIAGNOSE existing evidence')
            pilot._node_evidence(self.plan_id,self.ctx['pilot'],self.ctx)
        self.assertEqual(response['REALITY']['tool_contract_sha256'],contract['sha256'])
        self.assertEqual(response['execution'],'NOT_REQUESTED')
        events=self.ctx['store'].read_events(self.plan_id+'-t1')
        self.assertTrue(events['chain_intact'])
        node=next(e for e in events['events'] if e['step']=='node.evidence')
        self.assertEqual(node['detail']['tool_contract_sha256'],contract['sha256'])
        self.assertIs(self.ctx['tool_registry'],registry)

    def save_plan(self):
        (self.ctx["state_dir"] / "plans" / f"{self.plan_id}.json").write_text(
            json.dumps({**self.plan.to_dict(), "pilot_binding": self.binding}))

    def chat(self, text, request_id="r1", session_id="s1"):
        return pilot.discuss({"plan_id": self.plan_id, "session_id": session_id,
                              "request_id": request_id, "text": text}, self.ctx)

    def tick(self):
        return pilot.reconcile({"plan_id": self.plan_id}, self.ctx)

    def feed(self, task="t1", **candidate_changes):
        planned = next(t for t in self.plan.tasks if t.task_id == task)
        binding = {"mission_id": self.plan.mission_id, "task_id": executor.namespaced_id(self.plan, task),
                   "owner_node": "AV15", "source_sha": self.sha,
                   "scope_digest": planned.execution_contract["scope_digest"],
                   "endpoint": "https://fixture.invalid/mcp", "subject": "FIXTURE_TRANSPORT_ONLY"}
        now = time.time()
        payload = {k: v for k, v in binding.items() if k not in {"endpoint", "subject"}}
        payload.update(schema="KMCW_CAPABILITY_EVIDENCE_V1", observed_at=now - 1, expires_at=now + 50,
                       candidates=[{"provider": "qwen-local", "available": True, "observed_at": now - 1,
                       "node_id": "AV15", "node_online": True, "heartbeat_at": now - 1,
                       "tested_capabilities": ["bounded-edit"], "capability_evidence_ref": "fixture:capability",
                       "qualification_status": "PASS", "capability_evidence_sha256": "a" * 64,
                       "runtime_artifact_sha256": "b" * 64, "context_limit_tokens": 4096,
                       "security_permissions": ["scoped-edit"], **candidate_changes}])
        raw = json.dumps(payload, sort_keys=True).encode()
        auth = {"authenticated": True, "subject": binding["subject"], "endpoint": binding["endpoint"],
                "permissions": ["capability:read"], "body_sha256": hashlib.sha256(raw).hexdigest()}
        self.ctx.setdefault("capability_inputs", {})[binding["task_id"]] = {
            "raw": raw, "binding": binding, "authentication": auth, "context_tokens": 100}
        return self.ctx["capability_inputs"][binding["task_id"]]

    def audit_pass(self):
        return mock.patch.object(executor, "run_mission_audit", side_effect=lambda stage, context: {
            "stage": stage, "gate_passed": True,
            "context_digest": executor.context_digest(stage, context)})

    def test_duplicate_mission_is_discovered_without_creation(self):
        before = list((self.ctx["state_dir"] / "plans").glob("*.json"))
        response = self.chat("MISSION discover existing mission")
        self.assertFalse(response["created"])
        self.assertEqual(response["mission_id"], self.plan.mission_id)
        self.assertEqual(before, list((self.ctx["state_dir"] / "plans").glob("*.json")))

    def test_thai_english_intents_and_suggestions_do_not_authorize(self):
        for text, expected in [("ช่วยวางแผนแก้งาน", "PLAN"), ("PLAN repair", "PLAN"),
            ("ลงมือแก้งานที่อนุมัติแล้ว", "EXECUTE"), ("EXECUTE existing task", "EXECUTE"),
            ("Should we execute this?", "ADVISE"), ("ควรรันเลยดีไหม", "ADVISE"),
            ("อธิบายทางเลือก", "ADVISE"), ("สถานะงาน", "STATUS"),
            ("วิเคราะห์สาเหตุ", "DIAGNOSE"), ("ARCHITECT existing system", "ARCHITECT")]:
            with self.subTest(text=text):
                self.assertEqual(conversation.route_intent(text), expected)
        with mock.patch.object(pilot, "reconcile") as run:
            response = self.chat("EXECUTE existing task")
            self.assertIn("BLOCKED_VERIFIED", response["execution"])
            run.assert_not_called()

    def test_architecture_references_are_actual_bytes(self):
        response = self.chat("ARCHITECT existing system")
        for path, digest in response["REALITY"]["architecture_refs"].items():
            self.assertEqual(digest, hashlib.sha256((Path(self.repo) / path).read_bytes()).hexdigest())
        self.assertEqual(response["ASSUMPTION"], [])
        self.assertEqual(response["PROPOSAL"][0]["status"], "PROPOSED_ONLY")

    def test_decomposition_preserves_identity_and_bounds(self):
        self.assertEqual(self.plan.mission_id, "G0-G1-MINIMUM-SELLABLE-DEMO")
        self.assertEqual(self.plan.plan_id, self.plan_id)
        self.assertEqual([t.task_id for t in planner.order_tasks(self.plan)], ["t1", "t2", "verify"])
        with self.assertRaises(planner.PlanError):
            planner.decompose_plan(self.plan, self.units * 8, self.sha)

    def test_dependencies_do_not_accept_executor_claimed_done(self):
        self.feed("t1")
        with self.audit_pass(), mock.patch.object(executor, "execute_plan", return_value={
                "tasks": [{"task_id": "t1", "status": "DONE"}], "status": "DONE"}) as execute:
            report = self.tick()
        self.assertEqual(execute.call_args.kwargs["selected_task_ids"], ["t1"])
        self.assertFalse(report["receipts"]["t1"]["verified"])
        self.assertNotEqual(report["status"], "DONE")
        self.assertEqual(report["acceptance"]["percent"], 0)

    def test_provider_routing_requires_authenticated_current_feed(self):
        self.feed("t1")
        with self.audit_pass(), mock.patch.object(executor, "execute_plan", return_value={"tasks": []}) as execute:
            report = self.tick()
        self.assertTrue(report["dispatch"])
        self.assertEqual(report["tasks"][0]["provider_routing"]["selected_provider"], "qwen-local")
        self.assertFalse(execute.call_args.kwargs["provider_policy"]["external_authorized"])
        self.assertEqual(report["kmnw"], "PREVIEW_ONLY")

    def test_offline_unqualified_nodes_cannot_dispatch(self):
        for change in ({"node_online": False}, {"qualification_status": "PROPOSED"}):
            self.feed("t1", **change)
            self.feed("t2", **change)
            with self.audit_pass(), mock.patch.object(executor, "execute_plan") as execute:
                report = self.tick()
            execute.assert_not_called()
            self.assertFalse(report["dispatch"])

    def test_concurrent_tick_cannot_claim_or_dispatch(self):
        path = self.ctx["store"].checkpoints / f".reconcile-{self.plan_id}.lock"
        with path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(executor, "execute_plan") as execute:
                report = self.tick()
            self.assertEqual(report["reason"], "CONTROLLER_TICK_ACTIVE")
            execute.assert_not_called()

    def test_restart_preserves_checkpoint_and_suppresses_same_blocker(self):
        with mock.patch.object(executor, "run_mission_audit", return_value={
                "gate_passed": False, "reason": "FIXTURE_AUTHORITY_MISSING"}) as audit:
            first = self.tick()
            self.ctx["store"] = QueueStore(self.ctx["store"].base)
            second = self.tick()
            self.assertEqual(audit.call_count, 2)  # one per independent task, no retry on restart
        self.assertTrue(first["tasks"][0]["notify_owner"])
        self.assertFalse(second["tasks"][0]["notify_owner"])
        self.assertTrue(second["tasks"][0]["unchanged_blocker"])
        self.generate.assert_not_called()

    def _install_structural_lesson_fixture(self):
        signature='REGISTRY_REBUILT_BINDING_LOSS'
        self.units[0]['goal']=signature+' repair calculator'
        self.plan=planner.decompose_plan(self.plan,self.units,self.sha);self.save_plan()
        registry=tools.build_default_registry();self.ctx['tool_registry']=registry
        first={'event':'learning.recorded','record_id':'isolated-structural-learning',
            'classification':'OBSERVED','source_sha':self.sha,
            'learning':executor.learning_record(problem='binding loss',cause=[{'ref':'isolated:before-after'}],
                criterion='retain original registered client')}
        first['record_sha256']=conversation._digest(first)
        task=self.plan.tasks[0]
        row={'event':'memory.indexed','record_id':'isolated-structural-index','classification':'OBSERVED',
            'error_signature':signature,'scope':{'repo':self.repo,'account_id':self.ctx['account_id'],
                'owner_uid':os.geteuid()},'source_sha':self.sha,
            'observed_at':time.strftime('%Y-%m-%dT%H:%M:%S+00:00',time.gmtime()),
            'source_files_sha256':{'calc.py':hashlib.sha256((Path(self.repo)/'calc.py').read_bytes()).hexdigest()},
            'execution_context':memory.execution_context(self.ctx,self.sha,task.allowed_paths,
                executor.namespaced_id(self.plan,'t1')),
            'learning_record_id':first['record_id'],'learning_record_sha256':first['record_sha256'],
            'memory_case':{'method_hint':'REENGINEER','PROBLEM':{'failure_class':signature},
                'CAUSE':{'evidenced':[{'ref':'isolated:before-after','fact':'registered binding was dropped'}]},
                'RESULT':{'state':'ISOLATED_PASS'},'SOLUTION_PATH':{'rule':'reuse original registry'}}}
        row['record_sha256']=conversation._digest(row)
        self.ctx['store'].save_checkpoint('.tool-audit','tool_policy_audit',{'events':[first,row]})
        return registry

    def test_retrieved_structural_lesson_causes_existing_inspection_not_just_readback(self):
        from coding_worker_v2.agent import self_development
        registry=self._install_structural_lesson_fixture()
        # Same isolated context; remove only the memory input for causal ablation.
        with mock.patch.object(memory,'planning_lessons',return_value={'lessons':[],'excluded':[]}), \
             mock.patch.object(self_development,'inspect',wraps=self_development.inspect) as inspect, \
             mock.patch.object(executor,'run_mission_audit',side_effect=lambda stage,scope:{'stage':stage,'context_digest':executor.context_digest(stage,scope),'gate_passed':False,'reason':'FIXTURE_GATE'}), \
             mock.patch.object(executor,'execute_plan') as run:
            baseline=self.tick();inspect.assert_not_called();run.assert_not_called()
        checkpoint=self.ctx['store'].checkpoints/('.reconcile-'+self.plan_id+'.json')
        self.assertTrue(checkpoint.resolve().is_relative_to(self.root.resolve()))
        checkpoint.unlink() # Controlled temporary counterfactual only, never active state.
        with mock.patch.object(self_development,'inspect',wraps=self_development.inspect) as inspect, \
             mock.patch.object(executor,'run_mission_audit',side_effect=lambda stage,scope:{'stage':stage,'context_digest':executor.context_digest(stage,scope),'gate_passed':False,'reason':'FIXTURE_GATE'}), \
             mock.patch.object(executor,'execute_plan') as run:
            enabled=self.tick();self.assertEqual(inspect.call_count,1);run.assert_not_called()
        old=baseline['tasks'][0];new=enabled['tasks'][0]
        self.assertEqual(old['method_selection']['method'],'NO_GROUNDED_METHOD')
        self.assertEqual(new['method_selection']['method'],'REENGINEER')
        self.assertEqual(new['method_result']['status'],'INSPECTED_EXISTING_SOURCE')
        self.assertIn('calc.py',new['method_result']['source_files'])
        self.assertEqual(new['reason'],'FIXTURE_GATE')
        self.assertFalse(enabled['dispatch'])
        # Persisted feedback prevents repeating the chosen inspection on identical input.
        with mock.patch.object(self_development,'inspect') as inspect:
            repeated=self.tick();inspect.assert_not_called()
        self.assertTrue(repeated['tasks'][0]['unchanged_blocker'])
        self.assertIs(self.ctx['tool_registry'],registry)

    def test_selected_method_reaches_original_executor_policy_and_feedback(self):
        self.feed('t1');self.feed('t2',node_online=False)
        lesson={'classification':'VERIFIED','context_comparison':{'state':'MATCH'},
            'provenance':{'original_ref':'isolated:verified-receipt','sha256':'a'*64},
            'recommendation':{'provider':'qwen-local'},'observed_cause':[]}
        with mock.patch.object(memory,'planning_lessons',return_value={'lessons':[lesson],'excluded':[]}), \
             self.audit_pass(),mock.patch.object(executor,'execute_plan',return_value={'tasks':[]}) as run:
            result=self.tick()
        self.assertEqual(run.call_count,1)
        selected=run.call_args.kwargs['provider_policy']['method_selection']
        self.assertEqual(selected['method'],'REUSE')
        self.assertFalse(selected['execution_authority'])
        saved=self.ctx['store'].load_checkpoint('.reconcile-'+self.plan_id)['data']
        self.assertEqual(saved['tasks']['t1']['method_selection'],selected)
        self.assertEqual(result['tasks'][0]['method_selection'],selected)

    def test_failed_learned_inspection_waits_and_adapts_only_on_changed_context(self):
        from coding_worker_v2.agent import self_development
        self._install_structural_lesson_fixture()
        def denied_gate(stage,scope):
            return {'stage':stage,'context_digest':executor.context_digest(stage,scope),
                    'gate_passed':False,'reason':'FIXTURE_GATE'}
        with mock.patch.object(self_development,'inspect',side_effect=ValueError('FIXTURE_SCOPE_UNAVAILABLE')) as inspect, \
             mock.patch.object(executor,'execute_plan') as run:
            first=self.tick();self.assertEqual(inspect.call_count,1);run.assert_not_called()
        self.assertEqual(first['tasks'][0]['method_result']['status'],'FAILED')
        with mock.patch.object(self_development,'inspect') as inspect:
            same=self.tick();inspect.assert_not_called()
        self.assertTrue(same['tasks'][0]['unchanged_blocker'])
        self.ctx['read_only']=True # Genuine narrower context, no new authority.
        with mock.patch.object(self_development,'inspect',wraps=self_development.inspect) as inspect, \
             mock.patch.object(executor,'run_mission_audit',side_effect=denied_gate), \
             mock.patch.object(executor,'execute_plan') as run:
            adapted=self.tick();self.assertEqual(inspect.call_count,1);run.assert_not_called()
        row=adapted['tasks'][0]
        self.assertEqual(row['method_selection']['method'],'ADAPT')
        self.assertEqual(row['method_result']['status'],'INSPECTED_EXISTING_SOURCE')
        self.assertEqual(row['reason'],'FIXTURE_GATE')
        self.assertFalse(adapted['dispatch'])

    def test_next_round_retrieves_error_lesson_and_defers_same_execution(self):
        self.feed('t1');self.feed('t2',node_online=False)
        failure={'tasks':[{'task_id':'t1','next_action':'UNCHANGED_FAILURE_INPUT'}]}
        with self.audit_pass(),mock.patch.object(executor,'execute_plan',return_value=failure) as run:
            self.tick()
            signature='UNCHANGED_FAILURE_INPUT';now=time.strftime('%Y-%m-%dT%H:%M:%S+00:00',time.gmtime())
            learning={'event':'learning.recorded','record_id':'isolated-loop-learning',
                'classification':'OBSERVED','source_sha':self.sha,
                'learning':executor.learning_record(problem='unchanged failure',cause=[],criterion='no repeat')}
            learning['record_sha256']=conversation._digest(learning)
            task=self.plan.tasks[0]
            indexed={'event':'memory.indexed','record_id':'isolated-loop-memory','classification':'OBSERVED',
                'error_signature':signature,'scope':{'repo':self.repo,'account_id':self.ctx['account_id'],
                    'owner_uid':os.geteuid()},'source_sha':self.sha,'observed_at':now,
                'source_files_sha256':{'calc.py':hashlib.sha256((Path(self.repo)/'calc.py').read_bytes()).hexdigest()},
                'execution_context':memory.execution_context(self.ctx,self.sha,task.allowed_paths,
                    executor.namespaced_id(self.plan,'t1')),
                'learning_record_id':learning['record_id'],'learning_record_sha256':learning['record_sha256'],
                'memory_case':{'PROBLEM':{'failure_class':signature},'CAUSE':{'evidenced':[]},
                    'RESULT':{'state':'ISOLATED_PASS'},'SOLUTION_PATH':{'rule':'preserve context, no repeated dispatch'}}}
            indexed['record_sha256']=conversation._digest(indexed)
            self.ctx['store'].save_checkpoint('.tool-audit','tool_policy_audit',{'events':[learning,indexed]})
            self.ctx['store']=QueueStore(self.ctx['store'].base)
            second=self.tick()
        self.assertEqual(run.call_count,1)
        row=second['tasks'][0]
        self.assertEqual(row['memory_error_signature'],signature)
        lesson=next(l for l in row['memory']['lessons'] if l['error_signature']==signature)
        self.assertEqual(lesson['context_comparison']['state'],'MATCH')
        self.assertIsNone(lesson['recommendation'])
        self.assertEqual(row['chosen_method'],'DEFER_UNCHANGED_FAILURE')
        self.assertTrue(row['unchanged_blocker'])
        self.generate.assert_not_called()

    def test_unchanged_timeout_never_retries_on_elapsed_backoff_after_restart(self):
        self.feed('t1');self.feed('t2',node_online=False)
        failed={'tasks':[{'task_id':'t1','failure_class':'TIMEOUT','next_action':'RETRY'}]}
        with self.audit_pass(),mock.patch.object(executor,'execute_plan',return_value=failed) as execute:
            self.tick()
            key='.reconcile-'+self.plan_id;saved=self.ctx['store'].load_checkpoint(key)['data']
            saved['tasks']['t1']['retry_at']=0
            self.ctx['store'].save_checkpoint(key,'controller_tick',saved)
            self.ctx['store']=QueueStore(self.ctx['store'].base)
            result=self.tick()
        self.assertEqual(execute.call_count,1)
        row=result['tasks'][0]
        self.assertTrue(row['unchanged_blocker'])
        self.assertEqual(row['chosen_method'],'DEFER_UNCHANGED_FAILURE')
        self.assertEqual(row['context_comparison']['state'],'MATCH')

    def test_changed_environment_revalidates_once_without_new_permissions(self):
        with mock.patch.object(executor,'run_mission_audit',return_value={
                'gate_passed':False,'reason':'FIXTURE_BLOCK'}) as audit:
            self.tick();self.tick()
            self.assertEqual(audit.call_count,2)
            with mock.patch('platform.machine',return_value='isolated-new-environment'):
                result=self.tick();self.tick()
            self.assertEqual(audit.call_count,4)
        row=result['tasks'][0]
        self.assertEqual(row['chosen_method'],'REVALIDATE_CHANGED_CONTEXT')
        self.assertIn('environment',row['context_comparison']['different_facets'])
        self.assertFalse(result['dispatch'])
        self.generate.assert_not_called()

    def test_legacy_blocker_without_context_is_not_retried_by_guard_upgrade(self):
        with mock.patch.object(executor,'run_mission_audit',return_value={
                'gate_passed':False,'reason':'FIXTURE_BLOCK'}) as audit:
            self.tick()
            key='.reconcile-'+self.plan_id;saved=self.ctx['store'].load_checkpoint(key)['data']
            for row in saved['tasks'].values():row.pop('execution_context',None)
            self.ctx['store'].save_checkpoint(key,'controller_tick',saved)
            result=self.tick()
        self.assertEqual(audit.call_count,2)
        self.assertEqual(result['tasks'][0]['context_comparison']['state'],'UNKNOWN')
        self.assertTrue(result['tasks'][0]['unchanged_blocker'])

    def test_retry_budget_and_backoff_are_enforced(self):
        self.feed("t1")
        self.feed("t2", node_online=False)
        failed = {"tasks": [{"task_id": "t1", "failure_class": "TIMEOUT", "next_action": "RETRY"}]}
        with self.audit_pass(), mock.patch.object(executor, "execute_plan", return_value=failed) as execute:
            self.tick()
            self.tick()
            self.assertEqual(execute.call_count, 1)
            store = self.ctx["store"]
            key = f".reconcile-{self.plan_id}"
            saved = store.load_checkpoint(key)["data"]
            saved["tasks"]["t1"].update(attempts=3, retry_at=0)
            store.save_checkpoint(key, "controller_tick", saved)
            report = self.tick()
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(report["tasks"][0]["reason"], "RETRY_BUDGET_EXHAUSTED")

    def test_changed_source_evidence_rechecks_gate_once(self):
        with mock.patch.object(executor, "run_mission_audit", return_value={
                "gate_passed": False, "reason": "FIXTURE_BLOCK"}) as audit:
            self.tick()
            self.tick()
            (Path(self.repo) / "calc.py").write_text("def add(a,b): return a+b\n")
            self.tick()
            self.assertEqual(audit.call_count, 4)

    def test_independent_task_continues_when_first_is_blocked(self):
        self.ctx["store"].save_checkpoint(".pilot-irrelevant", "fixture", {})
        readiness = executor.plan_readiness(self.plan, self.ctx["store"], self.ctx["receipts_dir"], self.repo,
                                            account_id=self.ctx["account_id"])
        readiness["nodes"][0].update(decision="BLOCKED", reason="FIXTURE_TASK_LOCAL_BLOCKER")
        self.feed("t2")
        with mock.patch.object(executor, "plan_readiness", return_value=readiness), self.audit_pass(), \
                mock.patch.object(executor, "execute_plan", return_value={"tasks": []}) as execute:
            report = self.tick()
        self.assertEqual(execute.call_args.kwargs["selected_task_ids"], ["t2"])
        self.assertEqual(report["tasks"][0]["reason"], "FIXTURE_TASK_LOCAL_BLOCKER")

    def test_memory_is_read_before_admit_and_routing(self):
        order = []
        with mock.patch.object(memory, "planning_lessons", side_effect=lambda *a, **k:
                order.append("memory") or {"lessons": []}), \
                mock.patch.object(executor, "run_mission_audit", side_effect=lambda *a:
                order.append("audit") or {"gate_passed": False, "reason": "fixture"}):
            self.tick()
        self.assertEqual(order[:2], ["memory", "audit"])

    def test_memory_verified_recommendation_influences_plan_proposal(self):
        lesson = {"classification": "VERIFIED", "recommendation": {"provider": "qwen-local",
                  "test_command": "python3 -m unittest"}}
        with mock.patch.object(memory, "planning_lessons", return_value={"lessons": [lesson]}):
            enabled = self.chat("PLAN repair", request_id="enabled")
        with mock.patch.object(memory, "planning_lessons", return_value={"lessons": []}):
            baseline = self.chat("PLAN repair", request_id="baseline")
        self.assertEqual(enabled["PROPOSAL"][0]["task_dag"], baseline["PROPOSAL"][0]["task_dag"])
        self.assertEqual(enabled["PROPOSAL"][0]["memory_recommendations"], [lesson["recommendation"]])
        self.assertEqual(baseline["PROPOSAL"][0]["memory_recommendations"], [])

    def test_stale_lessons_are_excluded_and_unverified_cause_is_not_proven(self):
        base = {"timestamp": time.time(), "source_sha": self.sha,
                "provenance": {"original_ref": "fixture:receipt", "sha256": "a" * 64},
                "PROBLEM": {}, "CAUSE": {"evidenced": ["observed failure"]},
                "RESULT": {"state": "FAILED"}, "SOLUTION_PATH": {"provider": "untrusted"},
                "verified_for_current_source": False}
        with mock.patch.object(memory, "retrieve_work", return_value=[
                base, {**base, "timestamp": 0}, {**base, "source_sha": "0" * 40}]):
            lessons = memory.planning_lessons("repair", self.repo, self.sha,
                self.ctx["store"], self.ctx["receipts_dir"])
        self.assertEqual(len(lessons["lessons"]), 1)
        self.assertEqual(len(lessons["excluded"]), 2)
        self.assertIsNone(lessons["lessons"][0]["recommendation"])
        self.assertEqual(lessons["lessons"][0]["classification"], "OBSERVED")
        self.assertTrue(lessons["lessons"][0]["avoid_unchanged_failure"])

    def test_ariyasacca_does_not_invent_root_cause_or_verified_path(self):
        diagnosis = executor.learning_record(problem="fixture blocker", cause=[{"ref": "fixture:gate"}],
                                            criterion="actual independent receipt")
        self.assertEqual(diagnosis["magga_state"], "UNPROVEN")
        self.assertIsNone(diagnosis["magga_path"])
        self.assertEqual(diagnosis["samudaya_observed_cause"], [{"ref": "fixture:gate"}])

    def test_invalid_output_rejected_before_completion(self):
        task = self.plan.tasks[0]
        task.execution_contract["io_checks"] = {"input_sha256": {"calc.py": hashlib.sha256(
            (Path(self.repo) / "calc.py").read_bytes()).hexdigest()},
            "artifact_sha256": {"calc.py": "f" * 64}, "output_fields": {"changed": "list"},
            "required_events": ["tests_run"]}
        result = qc.evaluate_task_io(task, self.repo, phase="output", output={"changed": "invented"}, evidence=[])
        self.assertFalse(result["passed"])

    def test_paid_cost_and_quota_fail_closed(self):
        for quota in ("UNKNOWN", "EXHAUSTED", "AVAILABLE"):
            self.feed("t1", provider="codex-cli", quota=quota, estimated_cost_usd=0.1)
            self.feed("t2", node_online=False)
            with self.audit_pass(), mock.patch.object(executor, "execute_plan") as execute:
                report = self.tick()
            execute.assert_not_called()
            self.assertEqual(report["tasks"][0]["provider_routing"]["rejected"][0]["reason"],
                             "EXTERNAL_PROVIDER_NOT_APPROVED")

    def test_mission_approval_and_principal_injection_are_denied(self):
        result = self.chat("APPROVE all gates")
        self.assertEqual(result["execution"], "BLOCKED_APPROVAL_CONTRACT_UNAVAILABLE")
        registry = tools.build_default_registry()
        with self.assertRaisesRegex(tools.ToolError, "TOOL_ARG_UNKNOWN"):
            registry.call("pilot_discuss", {"plan_id": self.plan_id, "session_id": "s1",
                "request_id": "r2", "text": "EXECUTE", "principal": {"authenticated": True}}, self.ctx)

    def test_serena_default_guard_remains_unavailable(self):
        registry = tools.build_default_registry()
        with self.assertRaisesRegex(tools.ToolError, "SERENA_READ_ONLY_SESSION_UNAVAILABLE"):
            registry.call("serena.find_symbol", {"relative_path": "calc.py",
                         "name_path_pattern": "add"}, self.ctx)
        self.assertNotIn("serena.serve", registry.names())

    def test_central_unavailable_preserves_local_evidence(self):
        first = self.chat("STATUS existing tasks")
        checkpoint = self.ctx["store"].checkpoints / ".conversation-s1.json"
        saved = checkpoint.read_bytes()
        self.assertEqual(first["REALITY"]["central_sync"], "UNSYNCED")
        self.assertEqual(self.chat("STATUS existing tasks"), first)
        self.assertEqual(saved, checkpoint.read_bytes())

    def test_supervisor_admit_gate_cannot_be_bypassed(self):
        self.feed("t1")
        with mock.patch.object(executor, "run_mission_audit", return_value={
                "gate_passed": False, "reason": "ADMIT_BLOCKED"}), \
                mock.patch.object(executor, "execute_plan") as execute:
            report = self.tick()
        self.assertFalse(report["dispatch"])
        execute.assert_not_called()

    def test_existing_identity_source_and_checkpoint_bytes_preserved(self):
        plan_file = self.ctx["state_dir"] / "plans" / f"{self.plan_id}.json"
        before = plan_file.read_bytes()
        with mock.patch.object(executor, "run_mission_audit", return_value={"gate_passed": False}):
            self.tick()
        self.chat("STATUS")
        self.assertEqual(plan_file.read_bytes(), before)
        self.assertEqual(memory.current_base_commit(self.repo), self.sha)
        self.assertEqual(self.plan.mission_id, self.ctx["mission_id"])

    def test_session_restart_context_and_request_conflict(self):
        first = self.chat("ADVISE alternatives")
        self.ctx["store"] = QueueStore(self.ctx["store"].base)
        second = self.chat("PLAN existing work", request_id="r2")
        self.assertEqual(second["previous_request_id"], "r1")
        self.assertEqual(first["task_ids"], second["task_ids"])
        with self.assertRaisesRegex(tools.ToolError, "IDEMPOTENCY_CONFLICT"):
            self.chat("EXECUTE now", request_id="r1")

    def test_scoped_pause_resume_does_not_change_admit(self):
        def command(text, request_id):
            request = {"session_id": "s1", "request_id": request_id, "plan_id": self.plan_id, "text": text}
            self.ctx["owner_command"] = {"authenticated": True, "subject": "FIXTURE_OWNER_MIDDLEWARE",
                "permissions": ["mission:" + text.lower()], "source_sha": self.sha,
                "account_id": self.ctx["account_id"], "mission_id": self.plan.mission_id,
                "plan_id": self.plan_id, "request_sha256": conversation._digest(request)}
            return self.chat(text, request_id=request_id)
        self.assertEqual(command("PAUSE", "p")["execution"], "PAUSED")
        self.assertEqual(self.tick()["reason"], "OWNER_PAUSED")
        self.assertEqual(command("RESUME", "r")["execution"], "RESUMED_GATES_UNCHANGED")
        with mock.patch.object(executor, "run_mission_audit", return_value={
                "gate_passed": False, "reason": "ADMIT_BLOCKED"}):
            self.assertFalse(self.tick()["dispatch"])

    def test_bounded_executor_keeps_original_audit_scope_and_checks_external_deps(self):
        with self.assertRaisesRegex(executor.AgentError, "DEPENDENCY_RECEIPT_REQUIRED"):
            executor.execute_plan(self.plan, self.ctx["config"], self.ctx["store"], self.ctx["receipts_dir"],
                self.ctx["state_dir"], self.repo, selected_task_ids=["verify"])
        with mock.patch.object(executor, "_execute_one", return_value={"status": "BLOCKED"}) as execute, \
                self.audit_pass():
            report = executor.execute_plan(self.plan, self.ctx["config"], self.ctx["store"],
                self.ctx["receipts_dir"], self.ctx["state_dir"], self.repo,
                use_worktree=False, selected_task_ids=["t1"])
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(len(execute.call_args.args[14]["plan_snapshot"]["tasks"]), 3)

    def test_forged_done_without_independent_proof_or_receipt_never_counts(self):
        task = Task(self.plan.mission_id, executor.namespaced_id(self.plan, "t1"), "AV15",
                    TaskInput(self.repo, "Repair calculator"), status="DONE")
        path = write_receipt(self.ctx["receipts_dir"], task)
        proof = executor.completion_readback(self.plan, "t1", self.ctx["store"],
            self.ctx["receipts_dir"], self.repo, account_id=self.ctx["account_id"])
        self.assertFalse(proof["verified"])
        self.assertEqual(proof["acceptance_passed"], 0)
        self.assertTrue(path.is_file())

    def test_corrupt_controller_checkpoint_fails_closed(self):
        path = self.ctx["store"].checkpoints / f".reconcile-{self.plan_id}.json"
        path.write_text("corrupt")
        with self.assertRaisesRegex(executor.AgentError, "CHECKPOINT_UNREADABLE"):
            self.tick()

    def signed_fixture(self, *, self_verify=False):
        """Ephemeral test key, never a runtime verifier principal or sign-off."""
        from coding_worker_v2.tests.test_agent_verification import TestVerification
        from coding_worker_v2.agent import verification
        signer = TestVerification()
        signer.setUp()
        self.addCleanup(signer.tearDown)
        planned = self.plan.tasks[0]
        os.environ['KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY']=str(signer.public)
        hints = {'execution_context':memory.execution_context(self.ctx,self.sha,planned.allowed_paths,
                     executor.namespaced_id(self.plan,'t1')),
                 "account_id": self.ctx["account_id"], "base_commit": self.sha,
                 "plan_id": self.plan_id, "plan_digest": conversation._digest(self.plan.to_dict()),
                 "scope_digest": executor.SCOPE_DIGEST, "acceptance": planned.acceptance,
                 "task_execution_contract": planned.execution_contract,
                 "workspace": self.repo, "approved_scope": {"allowed_paths": planned.allowed_paths}}
        task = Task(self.plan.mission_id, executor.namespaced_id(self.plan, "t1"), "AV15",
                    TaskInput(self.repo, planned.goal, hints=hints), status="QC_PENDING",
                    output={"patch_digest": "c" * 64, "changed": [], "acceptance_checks": [
                        {"criterion": planned.acceptance[0], "passed": True, "evidence_ref": "fixture:test"}]})
        task.lease_token = "FIXTURE_LEASE_ONLY"
        candidate = write_receipt(self.ctx["receipts_dir"], task)
        request_path, request = verification.create_handoff(self.ctx["state_dir"] / "verifier_handoff",
            task, candidate, {"mission_id": self.plan.mission_id, "plan_id": self.plan_id,
            "plan_digest": hints["plan_digest"], "scope_digest": executor.SCOPE_DIGEST}, "AV15", "kmcw:fixture")
        signer.request_path, signer.request = request_path, request
        signer.request_payload = request["request"]
        signer.proof_path = request_path.with_name(request_path.name.replace(".request.json", ".proof.json"))
        signer.write_proof(verifier_id="AV15" if self_verify else "fixture-independent-ci")
        os.environ["KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY"] = str(signer.public)
        if self_verify:
            return signer, task
        proof = verification.read_signed_proof(request_path, signer.proof_path, signer.public)
        task.evidence = [{"event": "qc_checked", "qc": {"passed": True, "independent_verified": True}},
                         {"event": "independent_verification", "proof": proof}]
        task.status = "DONE"
        write_receipt(self.ctx["receipts_dir"], task)
        archive = self.ctx["store"].done / task.task_id / "task.json"
        archive.parent.mkdir(parents=True)
        archive.write_text(json.dumps(self.ctx["store"]._terminal_record(task)))
        return signer, task

    def test_signed_receipt_readback_and_verified_acceptance_count(self):
        self.signed_fixture()
        result = executor.completion_readback(self.plan, "t1", self.ctx["store"],
            self.ctx["receipts_dir"], self.repo, account_id=self.ctx["account_id"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["acceptance_passed"], 1)

    def test_forged_signature_or_post_qc_output_cannot_promote_done(self):
        signer, task = self.signed_fixture()
        proof = json.loads(signer.proof_path.read_text())
        proof["payload"]["evidence_refs"] = ["fixture:forged"]
        signer.proof_path.write_text(json.dumps(proof))
        result = executor.completion_readback(self.plan, "t1", self.ctx["store"],
            self.ctx["receipts_dir"], self.repo, account_id=self.ctx["account_id"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["acceptance_passed"], 0)

    def test_executor_signed_self_verification_is_rejected(self):
        from coding_worker_v2.agent.verification import read_signed_proof
        signer, _ = self.signed_fixture(self_verify=True)
        with self.assertRaises(VerificationError):
            read_signed_proof(signer.request_path, signer.proof_path, signer.public)

    def test_registry_exposes_existing_transport_contracts_and_denies_readonly_mutation(self):
        from coding_worker_v2.agent import mcp_server
        registry = tools.build_default_registry()
        names = {entry["name"] for entry in mcp_server._schemas(registry)}
        self.assertTrue({"pilot_discuss", "pilot_reconcile"}.issubset(names))
        with self.assertRaisesRegex(tools.ToolError, "PERMISSION_DENIED"):
            registry.call("pilot_reconcile", {"plan_id": self.plan_id}, {**self.ctx, "read_only": True})
        result = registry.call("pilot_discuss", {"plan_id": self.plan_id, "session_id": "s1",
                               "request_id": "tool-read", "text": "STATUS"}, self.ctx)
        self.assertEqual(result["intent"], "STATUS")

    def test_nonretryable_dispatch_is_not_repeated_after_restart(self):
        self.feed("t1")
        self.feed("t2", node_online=False)
        with self.audit_pass(), mock.patch.object(executor, "execute_plan", return_value={"tasks": []}) as execute:
            self.tick()
            self.ctx["store"] = QueueStore(self.ctx["store"].base)
            report = self.tick()
        self.assertEqual(execute.call_count, 1)
        self.assertTrue(report["tasks"][0]["unchanged_blocker"])

    def test_interrupted_dispatch_retains_canonical_task_and_does_not_repeat(self):
        self.feed("t1")
        self.feed("t2", node_online=False)
        with self.audit_pass(), mock.patch.object(executor, "execute_plan", side_effect=RuntimeError("fixture crash")):
            with self.assertRaisesRegex(RuntimeError, "fixture crash"):
                self.tick()
        with self.audit_pass(), mock.patch.object(executor, "execute_plan") as execute:
            result = self.tick()
        execute.assert_not_called()
        self.assertEqual(result["tasks"][0]["reason"], "RECONCILE_INTERRUPTED_TICK")
        self.assertEqual(result["tasks"][0]["canonical_task_id"], self.plan_id + "-t1")

    def test_remote_candidate_cannot_be_executed_as_local_qwen(self):
        self.feed("t1", node_id="P02")
        self.feed("t2", node_online=False)
        with self.audit_pass(), mock.patch.object(executor, "execute_plan") as execute:
            result = self.tick()
        execute.assert_not_called()
        self.assertEqual(result["tasks"][0]["reason"], "LOCAL_EXECUTOR_NODE_BINDING_MISMATCH")

    def test_done_and_progress_use_real_signed_fixture_readback(self):
        self.plan.tasks = self.plan.tasks[:1]
        self.save_plan()
        self.signed_fixture()
        with mock.patch.object(executor, "execute_plan") as execute:
            result = self.tick()
        execute.assert_not_called()
        self.assertEqual(result["status"], "DONE")
        self.assertEqual(result["acceptance"], {"passed": 1, "total": 1, "percent": 100.0})

    def test_actual_receipt_memory_changes_recommendation_on_same_fixture(self):
        baseline = self.chat("PLAN repair", request_id="baseline")
        self.signed_fixture()
        enabled = self.chat("PLAN repair", request_id="enabled")
        self.assertEqual(baseline["PROPOSAL"][0]["task_dag"], enabled["PROPOSAL"][0]["task_dag"])
        self.assertEqual(baseline["PROPOSAL"][0]["memory_recommendations"], [])
        self.assertEqual(len(enabled["PROPOSAL"][0]["memory_recommendations"]), 1)
        self.assertEqual(enabled['PROPOSAL'][0]['method_selection']['method'],'REUSE')
        self.assertTrue(enabled['PROPOSAL'][0]['method_selection']['evidence_refs'])
        self.assertEqual(enabled["REALITY"]["memory"]["lessons"][0]["classification"], "VERIFIED")

    def test_verified_method_requires_current_environment_context_match(self):
        self.signed_fixture()
        with mock.patch('platform.machine',return_value='changed-test-environment'):
            response=self.chat('PLAN repair',request_id='changed-context')
        self.assertEqual(response['PROPOSAL'][0]['memory_recommendations'],[])
        self.assertEqual(response['PROPOSAL'][0]['method_selection']['method'],'ADAPT')
        lesson=next(l for l in response['REALITY']['memory']['lessons'] if l['classification']=='VERIFIED')
        self.assertIsNone(lesson['recommendation'])
        self.assertIn('environment',lesson['context_comparison']['different_facets'])
        self.assertEqual(response['execution'],'NOT_REQUESTED')

    def test_post_qc_output_tamper_cannot_reuse_old_signed_candidate(self):
        signer, task = self.signed_fixture()
        task.output["acceptance_checks"].append({"criterion": "invented", "passed": True,
                                                "evidence_ref": "fixture:invented"})
        write_receipt(self.ctx["receipts_dir"], task)
        (self.ctx["store"].done / task.task_id / "task.json").write_text(
            json.dumps(self.ctx["store"]._terminal_record(task)))
        result = executor.completion_readback(self.plan, "t1", self.ctx["store"],
            self.ctx["receipts_dir"], self.repo, account_id=self.ctx["account_id"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["acceptance_passed"], 0)

    def test_forged_verified_memory_is_rejected_after_signature_readback(self):
        signer, _ = self.signed_fixture()
        proof = json.loads(signer.proof_path.read_text())
        proof["signature"] = "Zm9yZ2Vk"
        signer.proof_path.write_text(json.dumps(proof))
        lessons = memory.planning_lessons(self.plan.source_command, self.repo, self.sha,
            self.ctx["store"], self.ctx["receipts_dir"], account_id=self.ctx["account_id"], plan=self.plan)
        self.assertEqual(lessons["lessons"], [])
        self.assertEqual(lessons["excluded"][0]["classification"], "REJECTED")

    def test_historical_readback_does_not_change_live_proof_expiry_gate(self):
        from datetime import datetime, timedelta, timezone
        from coding_worker_v2.agent import verification
        from coding_worker_v2.tests.test_agent_verification import TestVerification
        signer = TestVerification()
        signer.setUp()
        self.addCleanup(signer.tearDown)
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        signer.request_payload["requested_at"] = old.isoformat()
        signer.request["request_digest"] = hashlib.sha256(
            verification._canonical(signer.request_payload)).hexdigest()
        signer.request_path.write_text(json.dumps(signer.request))
        signer.write_proof(issued_at=(old + timedelta(seconds=1)).isoformat(),
                           expires_at=(old + timedelta(minutes=5)).isoformat())
        with self.assertRaisesRegex(VerificationError, "STALE"):
            verification.read_signed_proof(signer.request_path, signer.proof_path, signer.public)
        checked = verification.read_signed_proof(signer.request_path, signer.proof_path, signer.public,
                                               verified_at=old + timedelta(seconds=2))
        self.assertTrue(checked["authenticated"])
        with self.assertRaises(VerificationError):
            verification.read_signed_proof(signer.request_path, signer.proof_path, signer.public,
                                           verified_at=datetime.now())

    def test_saved_provider_is_not_silently_replaced(self):
        self.ctx["pilot"].update(provider="copilot-cli", authorize_subscription=True)
        self.feed("t1")
        with self.audit_pass(), mock.patch.object(executor, "execute_plan") as execute:
            result = self.tick()
        execute.assert_not_called()
        self.assertEqual(result["tasks"][0]["reason"], "SAVED_PROVIDER_REQUIRES_RECONCILIATION")

    def test_session_bound_prevents_unbounded_checkpoint_growth(self):
        self.chat("STATUS")
        key = ".conversation-s1"
        saved = self.ctx["store"].load_checkpoint(key)["data"]
        saved["requests"] = {f"r{i}": {} for i in range(16)}
        self.ctx["store"].save_checkpoint(key, "conversation", saved)
        with self.assertRaisesRegex(tools.ToolError, "SESSION_BOUND_REACHED"):
            self.chat("STATUS", request_id="new-request")

    def test_signed_proof_cannot_substitute_other_scope_paths_or_executor(self):
        signer, task = self.signed_fixture()
        original = copy.deepcopy(task.evidence[-1]["proof"])
        for changes in ({"scope_digest": "sha256:" + "0" * 64}, {"allowed_paths": ["other.py"]},
                        {"executor_id": "P02"}, {"plan_id": "other-plan"}):
            altered = {**original, **changes}
            task.evidence[-1]["proof"] = altered
            write_receipt(self.ctx["receipts_dir"], task)
            (self.ctx["store"].done / task.task_id / "task.json").write_text(
                json.dumps(self.ctx["store"]._terminal_record(task)))
            with mock.patch("coding_worker_v2.agent.verification.read_signed_proof", return_value=altered):
                result = executor.completion_readback(self.plan, "t1", self.ctx["store"],
                    self.ctx["receipts_dir"], self.repo, account_id=self.ctx["account_id"])
            self.assertFalse(result["verified"])


if __name__ == "__main__":
    unittest.main()
