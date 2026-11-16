"""Regressions for independent source review R1-R5; isolated fixtures only."""
import json
import unittest
from pathlib import Path
from unittest import mock
from coding_worker_v2 import run_once
from coding_worker_v2.agent import executor, ingress, memory, pilot, planner, tools
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.tests import test_selfdev_ingress_tracking as channel_support
from coding_worker_v2.tests import test_self_development as repair_support


class CommandAcceptanceTest(unittest.TestCase):
    def setUp(self):
        helper=channel_support.IngressTrackingTest();helper.setUp()
        self.addCleanup(helper.doCleanups);self.helper=helper;self.ctx=helper.ctx;self.h=helper.fixture

    def online(self, **change):
        self.ctx['tracking_runtime']={'authenticated':True,'principal_id':'FIXTURE_RUNTIME',
            'owner_node':'AV15','mission_id':self.h.plan.mission_id,'source_sha':self.h.sha,
            'source_ref':'fixture:verified-liveness','observed_at':990,'freshness_until':1040,
            'online':True,**change}

    def test_R1_task_approval_cannot_execute_independent_sibling(self):
        self.online();self.h.feed('t2')
        readiness=executor.plan_readiness(self.h.plan,self.ctx['store'],self.ctx['receipts_dir'],self.h.repo,
                                         account_id=self.ctx['account_id'])
        readiness['nodes'][0].update(decision='BLOCKED',reason='FIXTURE_FIRST_TASK_BLOCKED')
        args=self.helper.command('kmcw.mission.control',payload={'action':'EXECUTE'},approval=True)
        with mock.patch.object(executor,'plan_readiness',return_value=readiness),self.h.audit_pass(), \
                mock.patch.object(executor,'execute_plan',return_value={'tasks':[]}) as execute:
            self.helper.call('kmcw.mission.control',args)
        execute.assert_not_called()

    def test_R1_task_approval_cannot_pause_entire_plan(self):
        args=self.helper.command('kmcw.mission.control',payload={'action':'PAUSE'},approval=True)
        with self.assertRaisesRegex(tools.ToolError,'PLAN_CONTROL_SCOPE_REQUIRED'):
            self.helper.call('kmcw.mission.control',args)
        self.assertIsNone(self.ctx['store'].load_checkpoint('.control-'+self.h.plan_id))

    def test_R1_declared_plan_scope_cannot_expand_principal_task_acl(self):
        ids=[executor.namespaced_id(self.h.plan,t.task_id) for t in self.h.plan.tasks]
        args=self.helper.command('kmcw.mission.control',payload={
            'action':'PAUSE','control_scope':'PLAN','task_ids':ids},approval=True)
        with self.assertRaisesRegex(tools.ToolError,'PLAN_CONTROL_SCOPE_REQUIRED'):
            self.helper.call('kmcw.mission.control',args)

    def test_R1_parent_execution_scope_cannot_be_widened(self):
        self.ctx['execution_task_ids']=[self.h.plan_id+'-t2']
        args=self.helper.command('kmcw.mission.control',payload={'action':'EXECUTE'},approval=True)
        with self.assertRaisesRegex(tools.ToolError,'PARENT_TASK_SCOPE_DENIED'):
            self.helper.call('kmcw.mission.control',args)

    def plan_control(self, action, command_id):
        ids=[executor.namespaced_id(self.h.plan,t.task_id) for t in self.h.plan.tasks]
        args=self.helper.command('kmcw.mission.control',payload={'action':action,'control_scope':'PLAN','task_ids':ids},
                                 approval=True,command_id=command_id)
        self.ctx['command_auth']['task_ids']=ids
        for channel in self.ctx['command_channels'].values():
            channel['principals']['fixture-owner']['task_ids']=ids
        return self.helper.call('kmcw.mission.control',args)

    def test_R2_distinct_pause_resume_pause_operations_do_not_collide(self):
        self.plan_control('PAUSE','pause-1')
        self.plan_control('RESUME','resume-1')
        self.plan_control('PAUSE','pause-2')
        checkpoint=self.ctx['store'].load_checkpoint('.control-'+self.h.plan_id)
        self.assertTrue(checkpoint['data']['paused'])

    def test_R4_definitely_unexecuted_offline_command_can_resume_on_verified_recovery(self):
        args=self.helper.command('kmcw.mission.control',payload={'action':'EXECUTE'},approval=True,command_id='same-operation')
        with mock.patch.object(pilot,'reconcile',return_value={'status':'BLOCKED'}) as execute:
            first=self.helper.call('kmcw.mission.control',args)
            self.assertEqual(first['status'],'UNAVAILABLE');execute.assert_not_called()
            self.online()
            recovered=self.helper.call('kmcw.mission.control',args)
            self.helper.call('kmcw.mission.control',args)
        self.assertEqual(execute.call_count,1)
        self.assertEqual(recovered['status'],'BLOCKED')

    def test_R4_unchanged_offline_and_ambiguous_pending_never_execute(self):
        args=self.helper.command('kmcw.mission.control',payload={'action':'EXECUTE'},approval=True)
        first=self.helper.call('kmcw.mission.control',args)
        key='.command-'+args['command']['idempotency_key']
        saved=self.ctx['store'].load_checkpoint(key)['data']
        saved['result']['status']='PENDING_READBACK'
        self.ctx['store'].save_checkpoint(key,'command_ingress',saved)
        self.online()
        with mock.patch.object(pilot,'reconcile') as execute:
            result=self.helper.call('kmcw.mission.control',args)
        execute.assert_not_called();self.assertEqual(result['status'],'PENDING_READBACK')

    def test_R4_changed_unqualified_liveness_cannot_restart_unavailable_command(self):
        args=self.helper.command('kmcw.mission.control',payload={'action':'EXECUTE'},approval=True)
        self.helper.call('kmcw.mission.control',args)
        with mock.patch.object(pilot,'reconcile') as execute:
            for change in ({'authenticated':False},{'online':False},{'owner_node':'P02'},
                           {'mission_id':'OTHER'},{'source_sha':'0'*40},{'freshness_until':999}):
                self.online(**change)
                result=self.helper.call('kmcw.mission.control',args)
                self.assertEqual(result['status'],'UNAVAILABLE')
        execute.assert_not_called()

    def test_R4_crash_after_execution_reservation_never_replays(self):
        self.online()
        args=self.helper.command('kmcw.mission.control',payload={'action':'EXECUTE'},approval=True)
        with mock.patch.object(pilot,'reconcile',side_effect=RuntimeError('fixture crash')) as execute:
            with self.assertRaises(tools.ToolError):self.helper.call('kmcw.mission.control',args)
            self.online(observed_at=995,freshness_until=1045)
            result=self.helper.call('kmcw.mission.control',args)
        self.assertEqual(execute.call_count,1)
        self.assertEqual(result['status'],'PENDING_READBACK')


class RepairAcceptanceTest(unittest.TestCase):
    def setUp(self):
        helper=repair_support.SelfDevelopmentTest();helper.setUp()
        self.addCleanup(helper.doCleanups);self.helper=helper;self.ctx=helper.ctx;self.h=helper.h

    def test_R3_first_durable_tested_checkpoint_has_complete_content_binding(self):
        _,task=self.helper.candidate();store=self.ctx['store'];store.enqueue(task);claimed=store.claim(task.task_id)
        result=run_once.run_task(claimed,store,self.ctx['config'],chain=[],
            before_mutation_fn=lambda t:{'gate_passed':True,'fixture_only':True},
            deterministic_edits=self.helper.proposal['edits'])
        self.assertEqual(result.status,'DONE')  # pipeline result, not final QC
        snapshot=store.load_checkpoint(task.task_id)['data']['task_snapshot']
        self.assertEqual(snapshot['output'].get('candidate_content_sha256'),
            memory.workspace_digest(task.task_input.repo,list(self.helper.proposal['source_files'])))

    def crash_resume(self, *, tamper=False, lease_alive=False):
        plan=self.h.plan;task=plan.tasks[0];task.max_repairs=0
        task.execution_contract['self_development']=self.helper.proposal
        task.execution_contract['scope_digest']=planner.task_scope_digest(plan,task,task.execution_contract)
        self.h.save_plan();self.h.feed('t1',provider='deterministic')
        actual=run_once.run_task
        class FixtureCrash(Exception):pass
        def after_pipeline(*args,**kwargs):
            result=actual(*args,**kwargs)
            self.assertEqual(result.status,'DONE')
            raise FixtureCrash('after first tested checkpoint, before executor handoff')
        with self.h.audit_pass(),mock.patch.object(run_once,'bounded_edit',wraps=run_once.bounded_edit) as edit, \
                mock.patch.object(run_once,'run_tests',wraps=run_once.run_tests) as tests, \
                mock.patch('coding_worker_v2.agent.qc.run_qc',return_value={
                    'passed':False,'independent_verified':False,'checks':[]}), \
                mock.patch.object(executor,'wait_signed_proof',side_effect=executor.VerificationError('VERIFIER_AUTHORITY_UNAVAILABLE')):
            with mock.patch.object(executor,'run_task',side_effect=after_pipeline):
                with self.assertRaises(FixtureCrash):pilot.reconcile({'plan_id':self.h.plan_id},self.ctx)
            key=self.h.plan_id+'-t1';lease_path=self.ctx['store'].doing/f'{key}.lease.json'
            lease=json.loads(lease_path.read_bytes())
            if not lease_alive:
                lease['expires_at']=0;lease_path.write_text(json.dumps(lease))
            if tamper:
                snapshot=self.ctx['store'].load_checkpoint(key)['data']['task_snapshot']
                (Path(snapshot['input']['repo'])/'test_calc.py').write_text('# changed tests\n')
            self.ctx['store']=QueueStore(self.ctx['store'].base)
            report=pilot.reconcile({'plan_id':self.h.plan_id},self.ctx)
        self.assertEqual(edit.call_count,1);self.assertEqual(tests.call_count,1)
        return report

    def test_R5_reserved_repair_budget_does_not_block_verified_tested_checkpoint_resume(self):
        report=self.crash_resume()
        self.assertTrue(report['dispatch'])
        row=next(r for r in report['tasks'] if r['task_id']=='t1')
        self.assertEqual(row['attempts'],1)
        self.assertIn('AWAIT_INDEPENDENT_VERIFICATION',row['reason'])
        self.assertFalse((self.ctx['store'].done/(self.h.plan_id+'-t1')/'task.json').exists())

    def test_R5_changed_candidate_tests_do_not_bypass_retry_budget(self):
        report=self.crash_resume(tamper=True)
        self.assertFalse(report['dispatch'])
        row=next(r for r in report['tasks'] if r['task_id']=='t1')
        self.assertEqual(row['reason'],'RETRY_BUDGET_EXHAUSTED')

    def test_R5_live_lease_cannot_be_reclaimed_as_checkpoint_recovery(self):
        report=self.crash_resume(lease_alive=True)
        self.assertFalse(report['dispatch'])
        self.assertTrue(self.ctx['store'].lease_valid(self.h.plan_id+'-t1'))


if __name__=='__main__':unittest.main()
