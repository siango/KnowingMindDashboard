"""Candidate isolation and deterministic task regressions; fixtures only."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock
from coding_worker_v2 import run_once
from coding_worker_v2.agent import executor, memory, planner, self_development
from coding_worker_v2.agent.worktree import WorktreeRegistry
from coding_worker_v2.tasks import Task, TaskInput
from coding_worker_v2.tests import test_unified_continuation as support


class SelfDevelopmentTest(unittest.TestCase):
    def setUp(self):
        helper = support.UnifiedContinuationTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.h = helper; self.ctx = helper.ctx
        inspection = self_development.inspect(helper.plan,'t1',self.ctx)
        observation = {'rc':1,'command':self.ctx['pilot']['test_command'],
            'source_files':inspection['source_files'],'output':'FIXTURE: assertion expected addition',
            'output_sha256':hashlib.sha256(b'FIXTURE: assertion expected addition').hexdigest()}
        path = self.ctx['state_dir'] / 'failure-fixture.json';path.write_text(json.dumps(observation))
        observation['evidence_ref'] = str(path)
        self.observation = observation
        self.proposal = self_development.propose(helper.plan,'t1',self.ctx,observation,
            [{'path':'calc.py','old':'return a - b','new':'return a + b'}],
            'Fixture assertion and bounded source show subtraction instead of addition')

    def test_reuse_inspection_keeps_original_registry_contract(self):
        from coding_worker_v2.agent import tools
        registry=tools.build_default_registry()
        self.ctx['tool_registry']=registry
        expected=registry.command_contract()['sha256']
        with mock.patch.object(tools,'build_default_registry',
                side_effect=AssertionError('must not replace bound registry')):
            result=self_development.inspect(self.h.plan,'t1',self.ctx)
        self.assertEqual(result['tool_contract_sha256'],expected)
        self.assertIs(self.ctx['tool_registry'],registry)

    def resign(self, proposal):
        proposal['proposal_sha256'] = self_development.digest({k:v for k,v in proposal.items() if k != 'proposal_sha256'})
        return proposal

    def task(self, candidate=None):
        plan=self.h.plan;planned=plan.tasks[0]
        scope={'mission_id':plan.mission_id,'queue_task_id':plan.plan_id+'-t1','owner_node':plan.owner_node,
            'goal':planned.goal,'plan_id':plan.plan_id,'plan_digest':'a'*64,'scope_digest':executor.SCOPE_DIGEST,
            'acceptance':planned.acceptance,'base_commit':self.h.sha,'revision':plan.revision,
            'source_command':plan.source_command,'allowed_paths':['calc.py']}
        return Task(plan.mission_id,plan.plan_id+'-t1',plan.owner_node,
            TaskInput(candidate or self.h.repo,planned.goal,test_command=self.ctx['pilot']['test_command'],max_repairs=0,
                hints={'account_id':self.ctx['account_id'],'approved_scope':scope,'plan_id':plan.plan_id,
                    'plan_digest':'a'*64,'acceptance':planned.acceptance,'base_commit':self.h.sha,
                    'workspace':self.h.repo,'isolated_worktree':candidate is not None,
                    'task_execution_contract':{'self_development':self.proposal}}),max_attempts=1)

    def candidate(self):
        registry=WorktreeRegistry(self.ctx['state_dir'])
        entry=registry.create(self.h.repo,self.h.plan_id+'-t1')
        task=self.task(entry['workspace_path'])
        self_development.stage_candidate(self.proposal,task,self.h.repo,registry)
        return registry,task

    def test_self_inspection_reads_scoped_source_history_and_qualifications(self):
        result=self_development.inspect(self.h.plan,'t1',self.ctx)
        self.assertEqual(result['source_sha'],self.h.sha)
        self.assertEqual(set(result['source_files']),{'calc.py','test_calc.py'})
        self.assertEqual(result['tool_qualification']['serena'],'NOT_QUALIFIED')
        self.assertEqual(result['central_sync'],'UNSYNCED')

    def test_proposal_requires_observed_failure_hash_and_minimal_unique_edit(self):
        for change in ({'output_sha256':'0'*64},{'rc':0}):
            observation={**self.observation,**change}
            Path(observation['evidence_ref']).write_text(json.dumps({k:v for k,v in observation.items() if k!='evidence_ref'}))
            with self.assertRaises(ValueError):
                self_development.propose(self.h.plan,'t1',self.ctx,observation,
                    [{'path':'calc.py','old':'return a - b','new':'return a + b'}],'fixture')
        with self.assertRaises(ValueError):
            self_development.propose(self.h.plan,'t1',self.ctx,self.observation,
                [{'path':'calc.py','old':'absent','new':'invented'}],'fixture')

    def test_proposal_scope_budget_and_hypothesis_cannot_authorize_execution(self):
        for change in ({'max_attempts':True},{'max_attempts':2},
                       {'root_cause':{'classification':'HYPOTHESIS','evidence_refs':['fixture']}},
                       {'edits':[{'path':'../outside','old':'x','new':'y'}]}):
            proposal=self.resign({**copy.deepcopy(self.proposal),**change})
            with self.subTest(change=change),self.assertRaises(ValueError):
                self_development.validate(proposal,['calc.py'],self.h.sha)

    def test_in_place_self_repair_denied_without_mutating_source(self):
        task=self.task();store=self.ctx['store'];store.enqueue(task);claimed=store.claim(task.task_id)
        before=(Path(self.h.repo)/'calc.py').read_bytes()
        with mock.patch.object(run_once,'bounded_edit') as edit:
            result=run_once.run_task(claimed,store,self.ctx['config'],chain=[],
                deterministic_edits=self.proposal['edits'])
        edit.assert_not_called()
        self.assertEqual(result.status,'FAILED')
        self.assertEqual(result.next_action,'SELF_REPAIR_ISOLATED_SCOPE_REQUIRED')
        self.assertEqual((Path(self.h.repo)/'calc.py').read_bytes(),before)

    def test_stage_requires_owned_candidate_and_preserves_dirty_candidate(self):
        with self.assertRaisesRegex(ValueError,'ISOLATED_CANDIDATE'):
            self_development.stage_candidate(self.proposal,self.task(),self.h.repo,None)
        registry=WorktreeRegistry(self.ctx['state_dir'])
        entry=registry.create(self.h.repo,self.h.plan_id+'-t1')
        path=Path(entry['workspace_path'])/'calc.py';path.write_text('# retained candidate edit\n')
        with self.assertRaisesRegex(ValueError,'DIRTY_CANDIDATE_RETAINED'):
            self_development.stage_candidate(self.proposal,self.task(entry['workspace_path']),self.h.repo,registry)
        self.assertEqual(path.read_text(),'# retained candidate edit\n')

    def test_changed_source_and_changed_proposal_do_not_overwrite_candidate(self):
        registry,task=self.candidate()
        proposal=self.resign({**copy.deepcopy(self.proposal),'rollback':'different candidate proposal'})
        with self.assertRaisesRegex(ValueError,'PROPOSAL_CONFLICT'):
            self_development.stage_candidate(proposal,task,self.h.repo,registry)

    def _tested_checkpoint(self):
        registry,task=self.candidate();store=self.ctx['store']
        store.enqueue(task);claimed=store.claim(task.task_id)
        result=run_once.run_task(claimed,store,self.ctx['config'],chain=[],
            before_mutation_fn=lambda t:{'gate_passed':True,'fixture_only':True},
            deterministic_edits=self.proposal['edits'])
        self.assertEqual(result.status,'DONE')  # pipeline execution only, NOT queue DONE/QC
        result.output['candidate_content_sha256']=memory.workspace_digest(task.task_input.repo,list(self.proposal['source_files']))
        store.checkpoint_task(result,'tested',{'phase':'tested','source_sha':self.h.sha,
            'workspace_digest':memory.workspace_digest(task.task_input.repo,['calc.py'])})
        return task,result

    def reclaim(self,task):
        store=self.ctx['store'];path=store.doing/f'{task.task_id}.lease.json'
        lease=json.loads(path.read_bytes());lease['expires_at']=0;path.write_text(json.dumps(lease))
        store.recover();return store.claim(task.task_id)

    def test_restart_reuses_tested_candidate_without_patch_or_test_duplication(self):
        task,result=self._tested_checkpoint();old=result.lease_token
        claimed=self.reclaim(task)
        self.assertNotEqual(old,claimed.lease_token)
        with mock.patch.object(run_once,'bounded_edit') as edit,mock.patch.object(run_once,'run_tests') as tests:
            resumed=run_once.run_task(claimed,self.ctx['store'],self.ctx['config'],chain=[],
                deterministic_edits=self.proposal['edits'])
        edit.assert_not_called();tests.assert_not_called()
        self.assertEqual(resumed.status,'DONE')
        self.assertTrue(any(e.get('event')=='candidate_test_checkpoint_reused' for e in resumed.evidence))
        self.assertFalse((self.ctx['store'].done/task.task_id/'task.json').exists())

    def test_modified_candidate_tests_invalidate_cached_pass(self):
        task,_=self._tested_checkpoint()
        (Path(task.task_input.repo)/'test_calc.py').write_text('# tampered test\n')
        claimed=self.reclaim(task)
        with mock.patch.object(run_once,'bounded_edit') as edit,mock.patch.object(run_once,'run_tests') as tests:
            resumed=run_once.run_task(claimed,self.ctx['store'],self.ctx['config'],chain=[],
                deterministic_edits=self.proposal['edits'])
        edit.assert_not_called();tests.assert_not_called()
        self.assertEqual(resumed.status,'FAILED')
        self.assertEqual(resumed.next_action,'SELF_REPAIR_TEST_CHECKPOINT_CONTENT_MISMATCH')

    def test_shared_before_mutation_gate_is_still_mandatory(self):
        _,task=self.candidate();store=self.ctx['store'];store.enqueue(task);claimed=store.claim(task.task_id)
        with mock.patch.object(run_once,'bounded_edit') as edit:
            result=run_once.run_task(claimed,store,self.ctx['config'],chain=[],
                before_mutation_fn=lambda t:{'gate_passed':False},deterministic_edits=self.proposal['edits'])
        edit.assert_not_called();self.assertEqual(result.next_action,'AUDIT_GATE_BLOCKED')

    def test_planner_binds_self_development_metadata_in_existing_task_scope(self):
        plan=self.h.plan;task=plan.tasks[0]
        task.execution_contract['self_development']=self.proposal
        with self.assertRaises(planner.PlanError):
            planner.validate_plan_dict(plan.to_dict())
        task.execution_contract['scope_digest']=planner.task_scope_digest(plan,task,task.execution_contract)
        validated=planner.validate_plan_dict(plan.to_dict())
        self.assertEqual(validated.mission_id,plan.mission_id)
        self.assertEqual(validated.plan_id,plan.plan_id)

    def test_missing_before_mutation_callback_is_not_execution_authority(self):
        _,task=self.candidate();store=self.ctx['store'];store.enqueue(task);claimed=store.claim(task.task_id)
        with mock.patch.object(run_once,'bounded_edit') as edit:
            result=run_once.run_task(claimed,store,self.ctx['config'],chain=[],deterministic_edits=self.proposal['edits'])
        edit.assert_not_called()
        self.assertEqual(result.next_action,'SELF_REPAIR_AUDIT_CALLBACK_REQUIRED')

    def test_executor_snapshot_cannot_expand_installed_context_scope(self):
        plan=self.h.plan;task=plan.tasks[0]
        task.execution_contract['self_development']=self.proposal
        task.execution_contract['scope_digest']=planner.task_scope_digest(plan,task,task.execution_contract)
        with self.h.audit_pass(),mock.patch.object(run_once,'bounded_edit') as edit:
            report=executor.execute_plan(plan,self.ctx['config'],self.ctx['store'],self.ctx['receipts_dir'],
                self.ctx['state_dir'],self.h.repo,selected_task_ids=['t1'],provider_policy={
                    'provider':'deterministic','account_id':self.ctx['account_id'],
                    'test_command':self.ctx['pilot']['test_command'],'context_paths':[]})
        edit.assert_not_called()
        self.assertEqual(report['tasks'][0]['reason'],'SELF_REPAIR_SNAPSHOT_SCOPE_DENIED')

    def test_current_executor_accepts_scoped_candidate_but_never_self_promotes(self):
        plan=self.h.plan;task=plan.tasks[0]
        task.execution_contract['self_development']=self.proposal
        task.execution_contract['scope_digest']=planner.task_scope_digest(plan,task,task.execution_contract)
        with self.h.audit_pass(),mock.patch('coding_worker_v2.agent.qc.run_qc',return_value={
                'passed':False,'independent_verified':False,'checks':[]}), \
                mock.patch('coding_worker_v2.agent.executor.wait_signed_proof',
                    side_effect=executor.VerificationError('VERIFIER_AUTHORITY_UNAVAILABLE')):
            report=executor.execute_plan(plan,self.ctx['config'],self.ctx['store'],self.ctx['receipts_dir'],
                self.ctx['state_dir'],self.h.repo,selected_task_ids=['t1'],provider_policy={
                    'provider':'deterministic','account_id':self.ctx['account_id'],
                    'test_command':self.ctx['pilot']['test_command'],
                    'context_paths':self.ctx['pilot']['context_paths']})
        from coding_worker_v2.receipts import read_receipt
        receipt=read_receipt(Path(report['tasks'][0]['receipt']))
        self.assertEqual(receipt['output']['candidate_source_sha'],self.h.sha)
        self.assertEqual(receipt['status'],'FAILED')
        self.assertEqual(receipt['next_action'],'AWAIT_INDEPENDENT_VERIFICATION')
        self.assertFalse((self.ctx['store'].done/receipt['task_id']).exists())
        self.assertIn('return a - b',(Path(self.h.repo)/'calc.py').read_text())

    def test_oversized_or_corrupt_failure_history_cannot_support_a_proposal(self):
        store=self.ctx['store'];task_id=self.h.plan_id+'-t1';path=store.events/f'{task_id}.jsonl'
        path.write_bytes(b'x'*1_000_001)
        with self.assertRaisesRegex(ValueError,'HISTORY_TOO_LARGE'):
            self_development.inspect(self.h.plan,'t1',self.ctx)
        path.unlink()
        event=store.append_event(task_id,'execution','FAILED','fixture:bad',mission_id=self.h.plan.mission_id)
        event.pop('delivery');event['status']='FORGED';path.write_text(json.dumps(event)+'\n')
        with self.assertRaisesRegex(ValueError,'EVENT_EVIDENCE_CORRUPT'):
            self_development.propose(self.h.plan,'t1',self.ctx,self.observation,
                [{'path':'calc.py','old':'return a - b','new':'return a + b'}],'fixture')


if __name__=='__main__': unittest.main()
