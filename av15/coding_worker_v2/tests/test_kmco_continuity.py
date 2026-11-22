"""Actual original QueueStore/receipt files; no live worker/authority assertions."""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from coding_worker_v2.agent.tools import build_default_registry, ToolError
from coding_worker_v2.agent.memory import current_base_commit
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.receipts import write_receipt
from coding_worker_v2.tasks import Task, TaskInput
from coding_worker_v2.tests import test_agent_executor as fixtures
from coding_worker_v2.tests.test_kmco_runtime import catalog


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.repo = fixtures.fixture_repo(self.root)
        self.ctx = {'workspace':self.repo,'store':QueueStore(self.root/'queue'),
                    'receipts_dir': self.root/'receipts','mission_id':'m','task_id':'t',
                    'account_id':'fixture-account','evidence':[]}
        self.ctx['receipts_dir'].mkdir()
        self.reg = build_default_registry(kmco_catalog=catalog())
        self.args = {'checkpoint_id':'c1','observation':{
            'phase':'tested','progress_units':1,'evidence_refs':['fixture:test'],
            'failure_class':'NONE','terminal':False}}

    def test_checkpoint_actual_readback_and_idempotency(self):
        first = self.reg.call('kmco.p07.checkpoint', self.args, self.ctx)
        second = self.reg.call('kmco.p07.checkpoint', self.args, self.ctx)
        self.assertFalse(first['reused']); self.assertTrue(second['reused'])
        self.assertEqual(first['checkpoint_sha256'],second['checkpoint_sha256'])
        self.assertEqual(len(list(self.ctx['store'].checkpoints.glob('.kmco-p07-*.json'))),1)
        self.assertFalse(first['dispatched'])
        self.args['observation']['progress_units']=2
        with self.assertRaisesRegex(ToolError,'IDEMPOTENCY_OR_SOURCE_CONFLICT'):
            self.reg.call('kmco.p07.checkpoint', self.args, self.ctx)

    def test_checkpoint_permission_and_schema_deny(self):
        with self.assertRaisesRegex(ToolError,'MCP_TOOL_SCOPE_DENIED'):
            self.reg.call('kmco.p07.checkpoint',self.args,self.ctx,mcp=True)
        with self.assertRaisesRegex(ToolError,'PERMISSION_DENIED'):
            self.reg.call('kmco.p07.checkpoint',self.args,{**self.ctx,'read_only':True})
        self.args['observation']['terminal']='false'
        with self.assertRaisesRegex(ToolError,'OBSERVATION_INVALID'):
            self.reg.call('kmco.p07.checkpoint',self.args,self.ctx)

    def add_receipt(self, ident, mission='m', account='fixture-account', sha=None):
        task=Task(mission,ident,'fixture-node',TaskInput(self.repo,'repair calculator',hints={
            'account_id':account,'base_commit':sha or current_base_commit(self.repo)}),status='DONE')
        return write_receipt(self.ctx['receipts_dir'],task,state_dir=self.root/'state')

    def test_scoped_receipt_recall_never_promotes_done_or_cross_domain(self):
        self.add_receipt('own');self.add_receipt('other-account',account='other')
        self.add_receipt('other-mission',mission='other')
        result=self.reg.call('kmco.p08.memory',{'goal':'repair calculator'},self.ctx)
        self.assertEqual([r['task_id'] for r in result['cases']],['own'])
        row=result['cases'][0]
        self.assertEqual(row['classification'],'OBSERVED_REQUIRES_INDEPENDENT_READBACK')
        self.assertEqual(row['freshness'],'CURRENT_SOURCE_OBSERVATION')
        self.assertEqual(result['independent_qc'],'NOT_RUN')

    def test_stale_source_memory_and_empty_request(self):
        self.add_receipt('old',sha='0'*40)
        result=self.reg.call('kmco.p08.memory',{'goal':'calculator'},self.ctx)
        self.assertEqual(result['cases'][0]['freshness'],'STALE')
        with self.assertRaisesRegex(ToolError,'P08_QUERY_LIMIT_INVALID'):
            self.reg.call('kmco.p08.memory',{'goal':'','limit':5},self.ctx)
        with self.assertRaisesRegex(ToolError,'MCP_TOOL_SCOPE_DENIED'):
            self.reg.call('kmco.p08.memory',{'goal':'calculator'},self.ctx,mcp=True)

    def test_nonmatching_and_out_of_scope_receipts_consume_read_budget(self):
        excluded = [None, {'mission_id':'other'},
                    {'mission_id':'m','input':{'hints':{'account_id':'other'}}},
                    {'mission_id':'m','input':{'hints':{'account_id':'fixture-account'},
                                              'goal':'unrelated'}}]
        for receipt in excluded:
            with self.subTest(receipt=receipt):
                examined=[]
                def receipts(root):
                    for index in range(2000):
                        examined.append(index)
                        yield self.root/'never-opened', receipt
                with patch('coding_worker_v2.agent.kmco_continuity._iter_receipts',receipts):
                    with self.assertRaisesRegex(ToolError,'P08_SCAN_BUDGET_EXCEEDED'):
                        self.reg.call('kmco.p08.memory',{'goal':'calculator','limit':1},self.ctx)
                self.assertEqual(len(examined),1001)

    def test_exact_read_budget_allows_empty_results(self):
        records=((self.root/'never-opened',None) for _ in range(1000))
        with patch('coding_worker_v2.agent.kmco_continuity._iter_receipts',return_value=records):
            result=self.reg.call('kmco.p08.memory',{'goal':'calculator'},self.ctx)
        self.assertEqual(result['cases'],[])
        self.assertEqual(result['independent_qc'],'NOT_RUN')
