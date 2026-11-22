import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from coding_worker_v2.agent.owner_cli import execute, load_owner_runtime, invoke
from coding_worker_v2.agent.circuit_cli import load_runtime
from coding_worker_v2.agent.tools import ToolError


class OwnerCircuitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        for args in [('init','-q'),('config','user.name','Fixture'),
                     ('config','user.email','fixture@example.invalid')]:
            subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True)
        (self.repo/'README').write_text('evidence')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','fixture'],check=True,capture_output=True)
        self.base=self.root/'runtime';(self.base/'queue').mkdir(parents=True)
        self.config=self.root/'runtime.json'
        self.doc={'schema':'KMCP_RUNTIME_V1','node_id':'fixture-node',
            'account_id':'posix:'+str(os.getuid()),'mission_id':'fixture-mission',
            'task_id':'fixture-task','workspace':str(self.repo),'runtime_base':str(self.base)}
        self.write()
        self.graph={'nodes':[{'id':'inspect','tool':'repo_inspect',
            'arguments':{'repo':str(self.repo)},'after':[],
            'result_schema':{'type':'object'}}],
            'outputs':{'repo':{'$ref':'inspect#'}},
            'acceptance':[{'path':'/repo','schema':{'type':'object'}}]}

    def write(self):
        self.config.write_text(json.dumps(self.doc));self.config.chmod(0o600)

    def test_preview_never_submits_or_runs(self):
        result=execute(self.config,self.graph,'inspect','request1')
        self.assertEqual(result['status'],'PREVIEW_ONLY')
        self.assertEqual(list((self.base/'queue/checkpoints').glob('.circuit*')),[])

    def test_real_local_uid_approval_runs_original_registry_and_receipt(self):
        result=execute(self.config,self.graph,'inspect','request1',approved=True)
        self.assertEqual(result['result']['status'],'COMPLETED')
        self.assertFalse(result['remote_admin_authority'])
        self.assertTrue(list((self.base/'queue/checkpoints').glob('.ssh-approval-*.json')))
        self.assertTrue(list((self.base/'receipts').glob('circuit-*.json')))
        again=execute(self.config,self.graph,'inspect','request1',approved=True)
        self.assertEqual(again['result']['status'],'COMPLETED')
        self.assertEqual(len(list((self.base/'receipts').glob('circuit-*.json'))),1)

    def test_native_owner_account_cannot_impersonate_admin(self):
        self.doc['account_id']='another-account';self.write()
        with self.assertRaises(ToolError):load_owner_runtime(self.config)

    def test_queue_must_already_exist(self):
        self.doc['runtime_base']=str(self.root/'missing');self.write()
        with self.assertRaises((ToolError,OSError)):load_owner_runtime(self.config)
        self.assertFalse((self.root/'missing').exists())

    def test_public_config_denied(self):
        self.config.chmod(0o644)
        with self.assertRaises(ToolError):load_owner_runtime(self.config)

    def test_destructive_and_database_grants_cannot_be_issued(self):
        ctx,reg=load_runtime(self.config)
        for tools in [('db.dhamma.read',),('kmcw.context',),('kmcp.command',)]:
            with self.subTest(tools=tools),self.assertRaises(ToolError):
                invoke(ctx,reg,'kmcp.circuit.submit',{},child_tools=tools,approved=True)

    def test_missing_explicit_approval_denied(self):
        ctx,reg=load_runtime(self.config)
        with self.assertRaises(ToolError):invoke(ctx,reg,'kmcp.circuit.run',{})

    def test_kves_projects_actual_registry_without_readiness_claim(self):
        from coding_worker_v2.agent.kves import projection
        ctx,reg=load_owner_runtime(self.config)
        view=projection(ctx,reg)
        self.assertEqual(len(view['nodes']),73)
        self.assertEqual(view['edges'],[])
        self.assertFalse(view['execution_authority'])
        self.assertTrue(all(n['live_readiness']=='NOT_PROBED_BY_PROJECTION' for n in view['nodes']))

    def test_kves_projects_executed_original_circuit_trace(self):
        from coding_worker_v2.agent.kves import projection
        run=execute(self.config,self.graph,'inspect','request1',approved=True)
        ctx,reg=load_owner_runtime(self.config)
        view=projection(ctx,reg,circuit_id=run['result']['circuit_id'])
        self.assertEqual(view['trace']['status'],'COMPLETED')
        self.assertEqual(view['nodes'][0]['trace']['status'],'DONE')
        self.assertFalse(view['ui_deployed'])

    def test_source_changed_prevents_same_request_replay(self):
        execute(self.config,self.graph,'inspect','request1',approved=True)
        (self.repo/'README').write_text('changed')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','changed'],check=True,capture_output=True)
        with self.assertRaises(ToolError):execute(self.config,self.graph,'inspect','request1',approved=True)


if __name__=='__main__':unittest.main()
