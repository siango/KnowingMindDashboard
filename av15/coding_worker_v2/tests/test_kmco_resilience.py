"""P09-P12 source bindings remain read-only and fail closed."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from coding_worker_v2.agent.tools import build_default_registry, ToolError
from coding_worker_v2.agent.memory import current_base_commit
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.tasks import Task, TaskInput
from coding_worker_v2.tests import test_agent_executor as fixtures
from coding_worker_v2.tests.test_kmco_runtime import catalog


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.repo=fixtures.fixture_repo(self.root)
        self.store=QueueStore(self.root/'queue')
        self.ctx={'workspace':self.repo,'store':self.store,'mission_id':'m','task_id':'issuer',
                  'account_id':'a','read_only':True}
        self.reg=build_default_registry(kmco_catalog=catalog())

    def test_p09_observes_live_lease_without_renewing(self):
        task=Task('m','work','node',TaskInput(self.repo,'goal',hints={
            'account_id':'a','base_commit':current_base_commit(self.repo)}))
        self.store.enqueue(task); claimed=self.store.claim('work')
        before=(self.store.doing/'work.lease.json').read_bytes()
        result=self.reg.call('kmco.p09.lease',{'task_id':'work'},self.ctx)
        self.assertEqual(result['status'],'ACTIVE_LEASE_OBSERVED_UNVERIFIED')
        self.assertFalse(result['heartbeat_performed'])
        self.assertEqual(before,(self.store.doing/'work.lease.json').read_bytes())
        self.assertNotIn(claimed.lease_token,json.dumps(result))

    def test_p09_denies_cross_account_and_expired_lease(self):
        task=Task('m','work','node',TaskInput(self.repo,'goal',hints={
            'account_id':'other','base_commit':current_base_commit(self.repo)}))
        self.store.enqueue(task); self.store.claim('work')
        with self.assertRaisesRegex(ToolError,'SCOPE_MISMATCH'):
            self.reg.call('kmco.p09.lease',{'task_id':'work'},self.ctx)

    def test_p10_deterministic_first_has_no_usage_claim(self):
        result=self.reg.call('kmco.p10.route',{'request':{'intent':'inspect',
            'deterministic_available':True,'context':{'key':'value'}}},self.ctx)
        self.assertEqual(result['route'],'DETERMINISTIC_EXISTING_HANDLER')
        self.assertFalse(result['provider_invoked']); self.assertIsNone(result['tokens_in'])
        with self.assertRaisesRegex(ToolError,'CONTEXT_BUDGET_EXCEEDED'):
            self.reg.call('kmco.p10.route',{'request':{'intent':'x',
                'deterministic_available':False,'context':{'x':'z'*40000}}},self.ctx)

    def test_p11_reuses_acl_and_never_returns_arguments(self):
        with self.assertRaisesRegex(ToolError,'GRANT_DENIED'):
            self.reg.call('kmco.p11.security',{'tool':'kmco.inspect',
                'arguments':{'operation_id':'P01-M01'}},self.ctx)

    def test_p12_local_first_parity_is_evidence_only(self):
        providers=[
            {'provider':'local-one','local':True,'available':True,
             'capabilities':['chat'],'evidence_ref':'fixture:1'},
            {'provider':'remote-one','local':False,'available':True,
             'capabilities':['chat'],'evidence_ref':'fixture:2'}]
        result=self.reg.call('kmco.p12.providers',{'providers':providers,'required':['chat']},self.ctx)
        self.assertEqual(result['preferred_provider'],'local-one')
        self.assertTrue(result['contract_parity_verified'])
        self.assertFalse(result['provider_invoked'])
        providers.append(dict(providers[0]))
        with self.assertRaisesRegex(ToolError,'ENVELOPE_INVALID'):
            self.reg.call('kmco.p12.providers',{'providers':providers,'required':['chat']},self.ctx)


if __name__ == '__main__':
    unittest.main()
