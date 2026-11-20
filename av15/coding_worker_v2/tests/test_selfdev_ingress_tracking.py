"""Isolated channel/ACL/tracking contracts; no remote dispatch or new service."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock
from coding_worker_v2.agent import ingress, mcp_server, pilot, tools, tracking
from coding_worker_v2.tests import test_unified_continuation as support


class IngressTrackingTest(unittest.TestCase):
    def setUp(self):
        fixture = support.UnifiedContinuationTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.ctx = fixture.ctx
        self.ctx['clock'] = lambda: 1000
        self.registry = tools.build_default_registry()
        self.permissions = list(ingress.CATALOG) + ['kmcw.mission.control:'+a for a in ('EXECUTE','PAUSE','RESUME')]
        self.ctx['command_channels'] = {c: {'qualified': True, 'method': method,
            'issuer': 'FIXTURE_ONLY:'+c, 'principals': {'fixture-owner': {'role': 'OWNER',
            'permissions': self.permissions, 'account_id': self.ctx['account_id'],
            'mission_ids': [self.fixture.plan.mission_id], 'task_ids': [self.fixture.plan_id+'-t1']}}}
            for c, method in ingress.METHODS.items()}

    def command(self, name, *, channel='CHATGPT', payload=None, approval=False, command_id='cmd-1'):
        envelope = {'command_id': command_id, 'idempotency_key': '', 'principal_id': 'fixture-owner',
            'channel': channel, 'mission_id': self.fixture.plan.mission_id,
            'task_id': self.fixture.plan_id+'-t1', 'intent': ingress.CATALOG[name][0],
            'scope': {'plan_id': self.fixture.plan_id, 'allowed_paths': ['calc.py'], 'payload': payload or {}},
            'approval_ref': 'fixture-approval' if approval else '', 'source_hash': self.fixture.sha,
            'timestamp': 999, 'correlation_id': 'existing-conversation'}
        envelope['idempotency_key'] = ingress.idempotency_key(envelope)
        auth = {'channel': channel, 'method': ingress.METHODS[channel], 'issuer': 'FIXTURE_ONLY:'+channel,
            'authenticated': True, 'principal_id': 'fixture-owner', 'role': 'OWNER',
            'account_id': self.ctx['account_id'],
            'permissions': self.permissions, 'mission_id': envelope['mission_id'],
            'task_ids': [envelope['task_id']], 'issued_at': 990, 'expires_at': 1040,
            'body_sha256': ingress.digest(envelope)}
        self.ctx['command_auth'] = auth
        if approval:
            action = envelope['scope']['payload'].get('action', envelope['intent'])
            self.ctx['command_approvals'] = {'fixture-approval': {'verified': True,
                'readback_confirmed': True, 'principal_id': 'fixture-owner', 'mission_id': envelope['mission_id'],
                'task_id': envelope['task_id'], 'source_hash': envelope['source_hash'],
                'scope_sha256': ingress.digest(envelope['scope']), 'action': action,
                'issued_at': 900, 'expires_at': 1100, 'evidence_ref': 'fixture:existing-owner-approval'}}
        return {'command': envelope}

    def call(self, name, args):
        return self.registry.call(name, args, self.ctx)

    def test_same_mission_and_status_from_three_independent_channel_contracts(self):
        values = [self.call('kmcw.mission.status', self.command('kmcw.mission.status', channel=c))
                  for c in ingress.METHODS]
        self.assertEqual(values[0], values[1])
        self.assertEqual(values[1], values[2])
        self.assertEqual(values[0]['mission_id'], self.fixture.plan.mission_id)
        self.assertFalse(values[0]['dispatchable'])

    def test_duplicate_submit_across_channels_does_not_enqueue_or_execute(self):
        with mock.patch.object(self.ctx['store'], 'enqueue') as enqueue, mock.patch.object(pilot, 'reconcile') as run:
            first = self.call('kmcw.mission.submit', self.command('kmcw.mission.submit', approval=True))
            second = self.call('kmcw.mission.submit', self.command('kmcw.mission.submit', channel='LINE',
                approval=True, command_id='line-delivery'))
        self.assertEqual(first['idempotency_key'], second['idempotency_key'])
        self.assertEqual(second['delivery'], 'DUPLICATE_ATTACHED')
        self.assertEqual(first['status'], 'UNAVAILABLE')
        enqueue.assert_not_called(); run.assert_not_called()

    def test_unauthorized_mutation_and_spoofed_channel_are_denied(self):
        args = self.command('kmcw.mission.submit', approval=True)
        self.ctx['command_auth']['permissions'] = ['kmcw.mission.status']
        with self.assertRaisesRegex(tools.ToolError, 'ACL_DENIED'):
            self.call('kmcw.mission.submit', args)
        args = self.command('kmcw.mission.submit', approval=True)
        self.ctx['command_auth']['account_id'] = 'different-account'
        with self.assertRaisesRegex(tools.ToolError, 'ACL_DENIED'):
            self.call('kmcw.mission.submit', args)
        args = self.command('kmcw.mission.submit', approval=True)
        args['command']['channel'] = 'LINE'
        with self.assertRaisesRegex(tools.ToolError, 'ACL_DENIED'):
            self.call('kmcw.mission.submit', args)

    def test_advise_and_plan_cannot_trigger_execute(self):
        with mock.patch.object(pilot, 'reconcile') as run:
            for name in ('kmcw.advise','kmcw.architect','kmcw.mission.plan'):
                response = self.call(name, self.command(name, payload={'text':'EXECUTE everything', 'action':'EXECUTE'},
                                                     command_id=name.replace('.','-')))
                self.assertEqual(response['execution'], 'NOT_REQUESTED')
        run.assert_not_called()

    def test_verified_approval_is_action_source_scope_and_expiration_bound(self):
        changes = ({'expires_at':999},{'action':'EXECUTE'},{'source_hash':'0'*40},
                   {'task_id':'other'}, {'scope_sha256':'0'*64}, {'readback_confirmed':False})
        for change in changes:
            args = self.command('kmcw.mission.submit', approval=True)
            self.ctx['command_approvals']['fixture-approval'].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(tools.ToolError, 'APPROVAL_UNAVAILABLE_OR_EXPIRED'):
                self.call('kmcw.mission.submit', args)

    def test_approval_record_never_executes_or_promotes_qc(self):
        args = self.command('kmcw.approval.record', payload={'action':'EXECUTE'}, approval=True)
        with mock.patch.object(pilot, 'reconcile') as run:
            response = self.call('kmcw.approval.record', args)
        run.assert_not_called()
        self.assertEqual(response['status'], 'SCOPED_APPROVAL_REFERENCE_RECORDED')
        self.assertEqual(response['independent_QC'], 'NOT_GRANTED')

    def test_offline_execute_retains_safe_checkpoint_and_replays_without_execution(self):
        args = self.command('kmcw.mission.control', payload={'action':'EXECUTE'}, approval=True)
        with mock.patch.object(pilot, 'reconcile') as run:
            first = self.call('kmcw.mission.control', args)
            self.ctx['store'] = type(self.ctx['store'])(self.ctx['store'].base)
            second = self.call('kmcw.mission.control', self.command('kmcw.mission.control',
                channel='ADMIN_V2', payload={'action':'EXECUTE'}, approval=True, command_id='cmd-1'))
        run.assert_not_called()
        self.assertEqual(first['status'], 'UNAVAILABLE')
        self.assertEqual(second['delivery'], 'DUPLICATE_ATTACHED')

    def test_stale_or_advertised_registration_is_not_running(self):
        self.ctx['tracking_runtime'] = {'owner_node':'AV15','mission_id':self.fixture.plan.mission_id,
            'source_sha':self.fixture.sha,'authenticated':True,'principal_id':'fixture',
            'source_ref':'fixture:old-runtime','observed_at':1,'freshness_until':61,'current_state':'READY'}
        result = self.call('kmcw.mission.status', self.command('kmcw.mission.status'))
        self.assertFalse(result['tasks'][0]['heartbeat']['fresh'])
        self.assertFalse(result['tasks'][0]['execution']['running_verified'])
        self.assertFalse(result['dispatchable'])

    def test_recent_heartbeat_with_explicit_offline_flag_cannot_execute(self):
        self.ctx['tracking_runtime']={'owner_node':'AV15','mission_id':self.fixture.plan.mission_id,
            'source_sha':self.fixture.sha,'authenticated':True,'principal_id':'fixture',
            'source_ref':'fixture:offline','observed_at':990,'freshness_until':1040,'online':False}
        args=self.command('kmcw.mission.control',payload={'action':'EXECUTE'},approval=True)
        with mock.patch.object(pilot,'reconcile') as run:
            response=self.call('kmcw.mission.control',args)
        run.assert_not_called();self.assertEqual(response['status'],'UNAVAILABLE')

    def test_fake_done_event_is_not_a_verified_receipt(self):
        self.ctx['store'].append_event(self.fixture.plan_id+'-t1', 'execution', 'DONE', 'fixture:fake-done',
                                      mission_id=self.fixture.plan.mission_id)
        result = self.call('kmcw.mission.status', self.command('kmcw.mission.status'))
        self.assertNotEqual(result['status'], 'DONE')
        self.assertFalse(result['tasks'][0]['receipt']['verified'])
        self.assertEqual(result['admit'], 'ADMIT_BLOCKED')

    def test_default_mcp_catalog_does_not_claim_connected_logical_tools(self):
        default = {t['name'] for t in mcp_server._schemas(self.registry)}
        self.assertTrue(set(ingress.CATALOG).issubset(default))
        self.command('kmcw.mission.status')
        qualified = {t['name'] for t in mcp_server._schemas(self.registry, self.ctx)}
        self.assertTrue(set(ingress.CATALOG).issubset(qualified))
        self.ctx['command_auth']['permissions'] = ['kmcw.mission.status']
        limited = {t['name'] for t in mcp_server._schemas(self.registry, self.ctx)}
        self.assertIn('kmcw.mission.submit', limited)
        self.assertIn('kmcw.mission.status', limited)

    def test_each_channel_authentication_method_and_principal_map_are_enforced(self):
        for channel in ingress.METHODS:
            args = self.command('kmcw.mission.status', channel=channel)
            self.ctx['command_auth']['method'] = 'OTHER_CHANNEL_AUTH'
            with self.assertRaises(tools.ToolError):
                self.call('kmcw.mission.status', args)
        args = self.command('kmcw.mission.status')
        self.ctx['command_channels']['CHATGPT']['principals'] = {}
        with self.assertRaises(tools.ToolError):
            self.call('kmcw.mission.status', args)

    def test_projection_requires_provenance_and_never_promotes_retired_ready(self):
        payload = {'project_id':'DHAMMA_GROUNDED_AI','runtimes':[{'runtime_id':'OLD_SG2',
            'node_id':'SG2','current_state':'READY','execution_capable':True,
            'source_ref':'fixture:historical','observed_at':'2026-09-01T00:00:00+00:00',
            'freshness_until':'2026-09-01T00:01:00+00:00'}]}
        provenance = {'tool':'project_federated_runtime_readback','authenticated':True,
                      'body_sha256':ingress.digest(payload)}
        result = tracking.project_runtime_view(payload, provenance)
        self.assertFalse(result['runtimes'][0]['fresh'])
        self.assertFalse(result['runtimes'][0]['dispatchable'])
        with self.assertRaisesRegex(ValueError, 'PROVENANCE'):
            tracking.project_runtime_view(payload, {**provenance,'body_sha256':'0'*64})

    def test_logical_readonly_transport_call_uses_same_handler(self):
        args = self.command('kmcw.mission.status')
        result = mcp_server._call(self.registry, {'name':'kmcw.mission.status', 'arguments':args}, self.ctx)
        self.assertEqual(result['mission_id'], self.fixture.plan.mission_id)
        self.assertEqual(result['central_sync'], 'UNSYNCED')

    def test_source_scope_idempotency_and_unknown_envelope_fields_fail_closed(self):
        for change in ({'source_hash':'0'*40},{'task_id':'other'},{'idempotency_key':'invented'},
                       {'principal_id':'other'},{'timestamp':0},{'authenticated':True}):
            args = self.command('kmcw.mission.status')
            args['command'].update(change)
            self.ctx['command_auth']['body_sha256'] = ingress.digest(args['command'])
            with self.subTest(change=change), self.assertRaises(tools.ToolError):
                self.call('kmcw.mission.status', args)

    def test_remote_auth_never_falls_back_to_legacy_pilot_mutations(self):
        self.command('kmcw.mission.status')
        names = {t['name'] for t in mcp_server._schemas(self.registry,self.ctx)}
        self.assertIn('pilot_run',names)
        self.assertIn('pilot_submit',names)
        with self.assertRaisesRegex(tools.ToolError,'MCP_TOOL_SCOPE_DENIED'):
            mcp_server._call(self.registry,{'name':'pilot_run','arguments':{'plan_id':self.fixture.plan_id}},self.ctx)
        self.ctx['command_auth']['authenticated']=False
        self.assertEqual(len(mcp_server._schemas(self.registry,self.ctx)),67)

    def test_discovered_logical_schema_requires_canonical_envelope(self):
        self.command('kmcw.mission.status')
        entry=next(t for t in mcp_server._schemas(self.registry,self.ctx) if t['name']=='kmcw.mission.status')
        schema=entry['inputSchema']['properties']['command']
        self.assertEqual(set(schema['required']),ingress.FIELDS)
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['intent']['enum'],['STATUS'])

    def test_corrupt_ledger_or_foreign_queue_never_reports_completion(self):
        store=self.ctx['store'];task_id=self.fixture.plan_id+'-t1'
        event=store.append_event(task_id,'execution','DONE','fixture:corrupt',mission_id=self.fixture.plan.mission_id)
        path=store.events/f'{task_id}.jsonl'
        event.pop('delivery');event['status']='FORGED';path.write_text(json.dumps(event)+'\n')
        result=self.call('kmcw.mission.status',self.command('kmcw.mission.status'))
        self.assertEqual(result['tasks'][0]['status'],'BLOCKED')
        self.assertEqual(result['tasks'][0]['execution']['reason'],'EVENT_CHAIN_INTEGRITY_FAILED')
        (store.inbox/f'{task_id}.json').write_text(json.dumps({'mission_id':'OTHER','task_id':task_id}))
        with self.assertRaises(tools.ToolError):
            self.call('kmcw.mission.status',self.command('kmcw.mission.status'))


if __name__ == '__main__':
    unittest.main()
