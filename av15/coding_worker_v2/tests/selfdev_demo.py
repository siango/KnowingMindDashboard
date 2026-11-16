"""Retain one isolated failing KMCW fixture; never execute the live GO-G1 task."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import mock
from coding_worker_v2.agent import executor, memory, pilot, planner, self_development
from coding_worker_v2.agent.sandbox import run_isolated
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.receipts import read_receipt
from coding_worker_v2 import run_once

PLAN_ID = 'pilot-0c3fdc91ed2e2a9011c0cb1d'
MISSION = 'G0-G1-MINIMUM-SELLABLE-DEMO'
TARGET = 'coding_worker_v2/agent/conversation.py'
TEST = 'coding_worker_v2/tests/test_selfdev_seed.py'
COMMAND = 'python3 -m unittest coding_worker_v2.tests.test_selfdev_seed -v'
INJECTION = "    if value == 'ADVISE injected fixture':\n        return 'EXECUTE'\n"


def _git(repo, *args):
    return subprocess.check_output(['git','-C',str(repo),*args], stderr=subprocess.STDOUT, text=True).strip()


def run(output):
    output = Path(output).resolve()
    result_path = output / 'demo-result.json'
    if result_path.is_file():
        saved = json.loads(result_path.read_bytes())
        if hashlib.sha256(Path(saved['active_fixture_file']).read_bytes()).hexdigest() != saved['active_sha256_after']:
            raise ValueError('DEMO_ACTIVE_FIXTURE_DRIFT')
        return {**saved, 'delivery':'CHECKPOINT_READBACK_NO_EXECUTION'}
    output.mkdir(parents=True, exist_ok=True)
    repo = output / 'fixture-source'
    if repo.exists():
        raise ValueError('DEMO_PARTIAL_FIXTURE_RETAINED_RECONCILIATION_REQUIRED')
    repo.mkdir()
    source = Path(__file__).resolve().parents[1]
    for folder in ('', 'agent'):
        dest = repo / 'coding_worker_v2' / folder
        dest.mkdir(parents=True, exist_ok=True)
        for path in (source / folder).glob('*.py'):
            shutil.copyfile(path, dest / path.name)
    tests = repo / 'coding_worker_v2/tests'
    tests.mkdir()
    shutil.copyfile(source / 'tests/__init__.py', tests / '__init__.py')
    (repo / TARGET).write_text((repo / TARGET).read_text().replace('    value = text.strip()\n',
                                                        '    value = text.strip()\n' + INJECTION, 1))
    (repo / TEST).write_text('''import unittest
from coding_worker_v2.agent.conversation import route_intent
from coding_worker_v2.agent.sandbox import policy_decision
class Seed(unittest.TestCase):
    def test_advice_never_executes(self):
        self.assertEqual(route_intent('ADVISE injected fixture'), 'ADVISE')
    def test_plan_regression(self):
        self.assertEqual(route_intent('PLAN existing work'), 'PLAN')
    def test_security_scope(self):
        self.assertEqual(policy_decision('edit','fixture','.',paths=['../outside'],
                         approved_paths=['../outside'],isolated=True)['decision'], 'REQUIRE_HUMAN_APPROVAL')
''')
    _git(repo, 'init','-q'); _git(repo,'config','user.email','fixture@local.invalid')
    _git(repo,'config','user.name','GO-G1 isolated fixture')
    _git(repo,'add','.'); _git(repo,'commit','-qm','Deliberately failing isolated KMCW fixture')
    config = {'schema':pilot.SCHEMA,'account_id':'selfdev-fixture-only','worker_id':'AV15',
              'workspace':str(repo),'runtime_base':str(output / 'runtime'),
              'allowed_paths':[TARGET],'context_paths':[TARGET,TEST],
              'test_command':COMMAND,'provider':'qwen-local'}
    config_path = output / 'fixture-config.json'
    config_path.write_text(json.dumps(config, indent=2))
    with mock.patch.dict(os.environ, {'KMW2_OWNER_NODE':'AV15', 'KMW2_VERIFIER_WAIT_S':'0'}):
        ctx = pilot.load_context(config_path)
        plan = planner.Plan(MISSION, PLAN_ID, 'AV15','Repair injected advice routing regression',
            tasks=[planner.PlannedTask('t1','Repair injected advice routing regression',
                    allowed_paths=[TARGET], acceptance=['advice regression and security tests pass'], max_repairs=0)])
        units = [{'task_id':name,'goal':goal,'deps':[],'allowed_paths':[TARGET],
                  'acceptance':['advice regression and security tests pass'],
                  'input_contract':['pinned candidate input'], 'output_contract':['bounded patch and test evidence'],
                  'evidence_required':['original failing test','candidate tests','independent promotion QC']}
                 for name,goal in [('t1','Repair injected advice routing regression'),
                                   ('next','Inspect candidate checkpoint evidence')]]
        plan = planner.decompose_plan(plan, units, memory.current_base_commit(str(repo)))
        inspection = self_development.inspect(plan, 't1', ctx)
        baseline = run_isolated(str(repo), COMMAND, 30,
            extra_env={'PYTHONPATH':str(repo),'PYTHONDONTWRITEBYTECODE':'1'})
        if baseline['rc'] == 0:
            raise AssertionError('Injected fixture did not fail')
        observation = {'command':COMMAND,'rc':baseline['rc'], 'source_files':inspection['source_files'],
                       'output_sha256':hashlib.sha256(baseline['output'].encode()).hexdigest(),
                       'output':baseline['output']}
        observation_path = ctx['state_dir'] / 'original-failure.json'
        observation_path.write_text(json.dumps(observation, indent=2))
        observation['evidence_ref'] = str(observation_path)
        proposal = self_development.propose(plan,'t1',ctx,observation,
            [{'path':TARGET,'old':INJECTION,'new':''}],
            'The isolated injected branch returns EXECUTE for the ADVISE test input; the assertion expects ADVISE.')
        plan.tasks[0].execution_contract['self_development'] = proposal
        plan.tasks[0].execution_contract['scope_digest'] = planner.task_scope_digest(
            plan,plan.tasks[0],plan.tasks[0].execution_contract)
        plan = planner.validate_plan_dict(plan.to_dict())
        binding = {'account_id':ctx['account_id'],'workspace':str(repo),'request_id':'existing-go-g1-fixture',
                   'after_task_id':'','test_command':COMMAND}
        (ctx['state_dir'] / 'plans' / f'{PLAN_ID}.json').write_text(json.dumps(
            {**plan.to_dict(),'pilot_binding':binding},indent=2))
        ns_id = PLAN_ID+'-t1'
        now = __import__('time').time()
        capability_binding = {'mission_id':MISSION,'task_id':ns_id,'owner_node':'AV15',
            'source_sha':inspection['source_sha'],'scope_digest':plan.tasks[0].execution_contract['scope_digest'],
            'endpoint':'https://fixture.invalid/mcp','subject':'FIXTURE_CAPABILITY_MIDDLEWARE'}
        payload = {k:v for k,v in capability_binding.items() if k not in {'endpoint','subject'}}
        payload.update(schema='KMCW_CAPABILITY_EVIDENCE_V1',observed_at=now-1,expires_at=now+50,candidates=[{
            'provider':'deterministic','node_id':'AV15','available':True,'observed_at':now-1,
            'node_online':True,'heartbeat_at':now-1,'tested_capabilities':['bounded-edit'],
            'capability_evidence_ref':'fixture:bounded-edit','qualification_status':'PASS',
            'capability_evidence_sha256':'a'*64,'runtime_artifact_sha256':'b'*64,
            'context_limit_tokens':4096,'security_permissions':['scoped-edit']}])
        raw = json.dumps(payload).encode()
        ctx['capability_inputs'] = {ns_id:{'raw':raw,'binding':capability_binding,'authentication':{
            'authenticated':True,'subject':capability_binding['subject'],'endpoint':capability_binding['endpoint'],
            'permissions':['capability:read'],'body_sha256':hashlib.sha256(raw).hexdigest()}}}
        active_before = hashlib.sha256((repo / TARGET).read_bytes()).hexdigest()
        def audit(stage, context):
            return {'gate_passed':True,'stage':stage,'context_digest':executor.context_digest(stage,context),
                    'fixture_only':True}
        # These are explicit local audit/QC test doubles, NOT live authority or independent sign-off.
        with mock.patch.object(executor,'run_mission_audit',side_effect=audit), \
             mock.patch('coding_worker_v2.agent.qc.run_qc',return_value={
                 'passed':False,'independent_verified':False,'checks':[{'name':'independent_verification','passed':False}]}), \
             mock.patch.object(run_once,'bounded_edit',wraps=run_once.bounded_edit) as edit, \
             mock.patch.object(run_once,'run_tests',wraps=run_once.run_tests) as test, \
             mock.patch('coding_worker_v2.providers.QwenProvider.generate',side_effect=AssertionError('No live inference')):
            first = pilot.reconcile({'plan_id':PLAN_ID},ctx)
            ctx['store'] = QueueStore(ctx['store'].base)
            second = pilot.reconcile({'plan_id':PLAN_ID},ctx)
            executions, test_runs = edit.call_count, test.call_count
        receipt = read_receipt(ctx['receipts_dir'] / f'{ns_id}.json')
        candidate = receipt['input']['repo']
        active_after = hashlib.sha256((repo / TARGET).read_bytes()).hexdigest()
        if executions != 1 or test_runs != 1 or active_before != active_after:
            raise AssertionError('Candidate isolation or duplicate-execution invariant failed')
        tests_ran = [e['tests'] for e in receipt['evidence'] if e.get('event') == 'tests_run']
        if not tests_ran or any(t['rc'] != 0 for t in tests_ran):
            raise AssertionError('Candidate tests did not pass')
        output_data = {'fixture_only':True,'mission_id':MISSION,'plan_id':PLAN_ID,'task_id':ns_id,
            'active_fixture_file':str(repo / TARGET),'active_sha256_before':active_before,'active_sha256_after':active_after,
            'master_source_unchanged_by_candidate':True,'candidate_workspace':candidate,
            'candidate_source_sha':receipt['output']['candidate_source_sha'],
            'candidate_content_sha256':receipt['output']['candidate_content_sha256'],
            'proposal':proposal,'inspection':inspection,'baseline':baseline,'candidate_tests':tests_ran,
            'patch_applications':executions,'candidate_test_invocations':test_runs,
            'restart_same_checkpoint':True,'first_controller':first,'resumed_controller':second,
            'next_dag_ready_task':PLAN_ID+'-next','next_dispatchable':False,
            'next_blocker':next(r.get('reason') for r in second['tasks'] if r['task_id']=='next'),
            'qc':'INDEPENDENT_PROMOTION_BLOCKED','receipt':str(ctx['receipts_dir'] / f'{ns_id}.json'),
            'receipt_status':receipt['status'],'candidate_output':receipt['output'],
            'memory':'LOCAL_OBSERVED_LESSON_ONLY','central_sync':'UNSYNCED','admit':'ADMIT_BLOCKED',
            'kmnw':'PREVIEW_ONLY','serena':'NOT_QUALIFIED','worker_model_calls':0,'paid_calls':0}
        result_path.write_text(json.dumps(output_data,indent=2)+'\n')
        return output_data


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    result=run(args.output)
    print(json.dumps({k:result[k] for k in ('fixture_only','task_id','candidate_source_sha','candidate_content_sha256',
        'patch_applications','candidate_test_invocations','receipt_status','qc','next_dag_ready_task','next_blocker')},indent=2))
