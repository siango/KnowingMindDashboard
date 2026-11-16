"""Changed transport behavior; authenticated claims here are fixtures, not live authority."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from coding_worker_v2.agent import capabilities, mcp_server, tools
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.tests.test_agent_executor import fixture_repo
from coding_worker_v2.agent.memory import current_base_commit


class CapabilityTest(unittest.TestCase):
    def test_admin_runtime_main_recovers_late_credential_and_rotation(self):
        from coding_worker_v2.agent import km_main
        requests = [{'jsonrpc':'2.0','id':1,'method':'initialize'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            *[{'jsonrpc':'2.0','id':i,'method':'tools/call',
               'params':{'name':'queue_health','arguments':{}}} for i in (3,4,5,6)],
            {'jsonrpc':'2.0','id':7,'method':'ping'}]
        source = io.BytesIO(('\n'.join(map(json.dumps, requests))+'\n').encode())
        sink = io.StringIO()
        first = mock.Mock(return_value={'account_id':'fixture-first'})
        rotated = mock.Mock(return_value={'account_id':'fixture-rotated'})
        actual_serve = mcp_server.serve
        def transport(ctx, **kwargs):
            resolver.assert_not_called()  # initialize/list must not need Admin.
            return actual_serve(ctx, source, sink, **kwargs)
        with mock.patch.object(km_main, '_ctx', return_value={}), \
             mock.patch('sys.argv', ['mcp_server','--base','fixture',
                 '--workspace','fixture','--admin-runtime-context']), \
             mock.patch.object(capabilities.KMAdminContextProducer,
                 'from_runtime_credentials', side_effect=[None,first,rotated,None]) as resolver, \
             mock.patch.object(mcp_server, 'serve', side_effect=transport), \
             mock.patch.object(mcp_server, '_call', return_value={'ok':True}) as call:
            self.assertEqual(mcp_server.main(),0)
        responses = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertIn('serverInfo',responses[0]['result'])
        self.assertIn('tools',responses[1]['result'])
        for index in (2,5):
            error = responses[index]['result']
            self.assertTrue(error['isError'])
            self.assertEqual(json.loads(error['content'][0]['text'])['reason'],
                             'KM_ADMIN_SESSION_CREDENTIAL_UNAVAILABLE')
        self.assertFalse(responses[3]['result']['isError'])
        self.assertFalse(responses[4]['result']['isError'])
        self.assertEqual([c.args[2]['account_id'] for c in call.call_args_list],
                         ['fixture-first','fixture-rotated'])
        self.assertEqual(resolver.call_count,4)
        first.assert_called_once(); rotated.assert_called_once()
        self.assertEqual(responses[6]['result'],{})

    def test_admin_runtime_main_without_flag_does_not_read_credentials(self):
        from coding_worker_v2.agent import km_main
        with mock.patch.object(km_main, '_ctx', return_value={}), \
             mock.patch('sys.argv', ['mcp_server','--base','fixture','--workspace','fixture']), \
             mock.patch.object(capabilities.KMAdminContextProducer,
                 'from_runtime_credentials') as resolver, \
             mock.patch.object(mcp_server, 'serve', return_value=0) as serve:
            self.assertEqual(mcp_server.main(),0)
        resolver.assert_not_called()
        serve.assert_called_once_with({},context_provider=None)

    def test_admin_malformed_session_role_denied_without_transport_crash(self):
        for role in ([],{},None,1):
            with self.subTest(role=role):
                producer = capabilities.KMAdminContextProducer('__Host-km_web='+'a'*64)
                with mock.patch.object(producer,'_read',return_value={
                        'authenticated':True,'role':role,'csrf':'fixture-only'}) as read:
                    requests = [{'jsonrpc':'2.0','id':1,'method':'initialize'},
                        {'jsonrpc':'2.0','id':2,'method':'tools/call',
                         'params':{'name':'queue_health','arguments':{}}},
                        {'jsonrpc':'2.0','id':3,'method':'ping'}]
                    source=io.BytesIO(('\n'.join(map(json.dumps,requests))+'\n').encode())
                    sink=io.StringIO()
                    with mock.patch.object(mcp_server,'_call') as call:
                        self.assertEqual(mcp_server.serve({},source,sink,
                            context_provider=producer),0)
                    call.assert_not_called()
                    read.assert_called_once_with('/api/session')
                    responses=[json.loads(line) for line in sink.getvalue().splitlines()]
                    error=json.loads(responses[1]['result']['content'][0]['text'])
                    self.assertEqual(error['reason'],'KM_ADMIN_OWNER_ADMIN_SESSION_REQUIRED')
                    self.assertEqual(responses[2]['result'],{})

    def test_v4_search_handoff_preserves_original_schema_without_identity(self):
        memory={'schema':'DHAMMA_MEMORY_SEARCH_V1','query':'MCP',
                'items':[{'memory_id':'fixture-search','evidence_status':'PROPOSED'}],
                'decisions':[]}
        bound=capabilities.bind_runtime_context({},lambda base:{'memory_context':memory})
        self.assertIs(bound['memory_context'],memory)
        self.assertNotIn('account_id',bound)
        self.assertNotIn('database_auth',bound)

    def test_v4_memory_handoff_is_advisory_and_preserves_real_handler(self):
        from coding_worker_v2.agent import km_main
        from coding_worker_v2.config import WorkerConfig
        import argparse
        memory={'schema':'DHAMMA_MEMORY_CONTEXT_V1','project_id':'DHAMMA_GROUNDED_AI',
                'items':[{'memory_id':'fixture-prior','evidence_status':'PROPOSED',
                          'content':'Unverified history is not execution authority'}],
                'decisions':[]}
        base={'workspace':self.ctx['workspace'],'store':self.ctx['store'],
              'receipts_dir':Path(self.tmp.name)/'receipts','config':WorkerConfig.from_env(),
              'tool_registry':self.reg,'read_only':True}
        bound=capabilities.bind_runtime_context(base,lambda scope:{'memory_context':memory})
        self.assertNotIn('account_id',bound)
        with mock.patch('builtins.print'):
            self.assertEqual(km_main.cmd_tools(argparse.Namespace(
                call='repo_inspect',args=json.dumps({'repo':self.ctx['workspace']}),yes=False),bound),0)
        events=bound['store'].load_checkpoint('.tool-audit')['data']['events']
        selected=next(e for e in events if e.get('event')=='tool_selected')
        expected=hashlib.sha256(json.dumps(memory,sort_keys=True,
            separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(selected['memory_context_sha256'],expected)
        with self.assertRaisesRegex(tools.ToolError,'TOOL_UNAVAILABLE'):
            mcp_server._call(self.reg,{'name':'repo_inspect',
                'arguments':{'repo':self.ctx['workspace']}},bound)
        self.assertEqual(memory['items'][0]['evidence_status'],'PROPOSED')

    def test_v4_memory_handoff_rejects_invalid_contract(self):
        for value in (None,{}, {'schema':'DHAMMA_MEMORY_CONTEXT_V1',
                'items':['untrusted'],'decisions':[]}):
            with self.assertRaisesRegex(tools.ToolError,'MCP_MEMORY_CONTEXT_INVALID'):
                capabilities.bind_runtime_context({},lambda scope:{'memory_context':value})

    def test_transport_refreshes_trusted_context_and_never_falls_back(self):
        requests = [{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
            *[{'jsonrpc':'2.0','id':i,'method':'tools/call',
               'params':{'name':'queue_health','arguments':{}}} for i in (2,3,4)],
            {'jsonrpc':'2.0','id':5,'method':'ping'}]
        source = io.BytesIO(('\n'.join(map(json.dumps, requests))+'\n').encode())
        sink = io.StringIO()
        provider = mock.Mock(side_effect=[{'command_auth':{'issued_at':1}},
            tools.ToolError('KM_ADMIN_CONTEXT_UNAVAILABLE'), {'command_auth':{'issued_at':2}}])
        with mock.patch.object(mcp_server,'_call',return_value={'ok':True}) as call:
            self.assertEqual(mcp_server.serve({'account_id':'stale'},source,sink,
                context_provider=provider),0)
        responses = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertFalse(responses[1]['result']['isError'])
        self.assertTrue(responses[2]['result']['isError'])
        self.assertFalse(responses[3]['result']['isError'])
        self.assertEqual(responses[4]['result'],{})
        self.assertEqual([c.args[2]['command_auth']['issued_at'] for c in call.call_args_list],
                         [1,2])
        self.assertEqual(provider.call_count,3)

    def test_reuse_pilot_call_preserves_original_registry_object(self):
        import argparse
        from coding_worker_v2 import pilot_install
        self.ctx['tool_registry']=self.reg
        with mock.patch.object(pilot_install,'load_context',return_value=self.ctx), \
             mock.patch.object(tools,'build_default_registry',
                side_effect=AssertionError('must reuse bound registry')), \
             mock.patch('builtins.print'):
            self.assertEqual(pilot_install.main(argparse.Namespace(
                pilot_action='call',config='existing-fixture',tool='queue_health',args='{}')),0)
            self.ctx['command_auth']['permissions']=[]
            with self.assertRaisesRegex(tools.ToolError,'MCP_TOOL_SCOPE_DENIED'):
                pilot_install.main(argparse.Namespace(
                    pilot_action='call',config='existing-fixture',tool='queue_health',args='{}'))

    def test_review_queue_resource_is_checked_on_every_call(self):
        before = self.ctx['store'].health()
        result = self.rpc([('tools/call', {'name':'queue_health','arguments':{}})])[0]
        self.assertFalse(result['result']['isError'])
        self.ctx['tool_bindings']['queue_health']['resource_ref'] = 'queue-store:/another-queue'
        denied = self.rpc([('tools/call', {'name':'queue_health','arguments':{}})])[0]
        self.assertTrue(denied['result']['isError'])
        self.assertIn('MCP_TOOL_SCOPE_DENIED', denied['result']['content'][0]['text'])
        self.assertEqual(self.ctx['store'].health(), before)
        for malformed in (None, [], 'invalid'):
            self.ctx['tool_bindings']['queue_health'] = malformed
            listed, denied, ping = self.rpc([('tools/list',{}),
                ('tools/call',{'name':'queue_health','arguments':{}}),('ping',{})])
            self.assertEqual(len(listed['result']['tools']),67)
            self.assertTrue(denied['result']['isError'])
            self.assertEqual(ping['result'],{})

    def test_review_admin_producer_requires_queue_resource(self):
        import copy
        origin=capabilities.KMAdminContextProducer.ORIGIN
        auth={**self.ctx['command_auth'],'channel':'ADMIN_V2','method':'SESSION_CSRF',
              'issuer':origin,'permissions':['queue_health']}
        role=self.ctx['command_channels']['CHATGPT']['principals']['fixture-principal']
        payload={'schema':'KMCW_VERIFIED_READ_CONTEXT_V1','execution_authority':False,
            **{k:self.ctx[k] for k in ('account_id','mission_id','task_id')},
            'command_auth':auth,
            'command_channels':{'ADMIN_V2':{'qualified':True,'method':'SESSION_CSRF',
                'issuer':origin,'principals':{'fixture-principal':role}}},
            'tool_bindings':copy.deepcopy(self.ctx['tool_bindings'])}
        del payload['tool_bindings']['queue_health']['resource_ref']
        producer=capabilities.KMAdminContextProducer('__Host-km_web='+'a'*64)
        with mock.patch.object(producer,'_read',side_effect=[
                {'authenticated':True,'role':'OWNER','csrf':'fixture'},payload]):
            with self.assertRaisesRegex(tools.ToolError,'KM_ADMIN_QUEUE_RESOURCE_REQUIRED'):
                producer(self.ctx)

    def test_review_protected_credential_resolver_reuses_file_and_denies_unsafe_files(self):
        import os
        directory=Path(self.tmp.name)/'credentials';directory.mkdir(mode=0o700)
        credential=directory/'km-ai-admin.session-cookie'
        credential.write_text('__Host-km_web='+'a'*64);credential.chmod(0o600)
        with mock.patch.dict(os.environ,{'CREDENTIALS_DIRECTORY':str(directory)}):
            producer=capabilities.KMAdminContextProducer.from_runtime_credentials()
            self.assertIsInstance(producer,capabilities.KMAdminContextProducer)
            credential.chmod(0o640)
            with self.assertRaisesRegex(tools.ToolError,'KM_ADMIN_CREDENTIAL_PERMISSIONS_DENIED'):
                capabilities.KMAdminContextProducer.from_runtime_credentials()
            credential.unlink();credential.symlink_to(directory/'absent')
            with self.assertRaisesRegex(tools.ToolError,'KM_ADMIN_CREDENTIAL_PERMISSIONS_DENIED'):
                capabilities.KMAdminContextProducer.from_runtime_credentials()
            credential.unlink();os.mkfifo(credential,0o600)
            with self.assertRaisesRegex(tools.ToolError,'KM_ADMIN_CREDENTIAL_PERMISSIONS_DENIED'):
                capabilities.KMAdminContextProducer.from_runtime_credentials()

    def test_reuse_original_pilot_context_ignores_ambient_admin_credentials(self):
        import argparse
        from coding_worker_v2 import pilot_install
        with mock.patch.object(pilot_install,'load_context',return_value=self.ctx), \
             mock.patch.object(capabilities.KMAdminContextProducer,'from_runtime_credentials',
                side_effect=AssertionError('legacy caller must not resolve new credentials')) as resolve, \
             mock.patch.object(mcp_server,'serve',return_value=0) as served:
            self.assertEqual(pilot_install.main(argparse.Namespace(pilot_action='mcp',config='fixture')),0)
        resolve.assert_not_called()
        self.assertIs(served.call_args.args[0],self.ctx)

    def test_review_source_failure_never_reads_runtime_credentials(self):
        import argparse
        from coding_worker_v2 import pilot_install
        with mock.patch.object(pilot_install,'load_context',
                side_effect=tools.ToolError('PILOT_INSTALLED_SOURCE_CHANGED')), \
             mock.patch.object(capabilities.KMAdminContextProducer,'from_runtime_credentials') as resolve:
            with self.assertRaisesRegex(tools.ToolError,'PILOT_INSTALLED_SOURCE_CHANGED'):
                pilot_install.main(argparse.Namespace(pilot_action='mcp',config='fixture'))
        resolve.assert_not_called()

    def test_review_disabled_activation_error_is_exact_and_redacted(self):
        from urllib import request,error
        producer=capabilities.KMAdminContextProducer('__Host-km_web='+'a'*64)
        with mock.patch.object(request,'build_opener') as opener:
            opener.return_value.open.side_effect=error.HTTPError('https://ai.knowingmind.app',503,'',{},
                io.BytesIO(b'{"error":"KMCW_DELEGATION_DISABLED","detail":"secret-canary"}'))
            with self.assertRaises(tools.ToolError) as caught:
                producer._read('/api/kmcw/context',csrf='fixture')
        self.assertEqual(caught.exception.detail,'503:KMCW_DELEGATION_DISABLED')
        self.assertNotIn('secret-canary',str(caught.exception))

    def test_review_readonly_handoff_rejects_null_and_nonboolean_values(self):
        base={**self.ctx,'read_only':True}
        for value in (None,0,1,'false',[],{}):
            with self.subTest(value=value), self.assertRaisesRegex(
                    tools.ToolError,'MCP_CONTEXT_READONLY_INVALID'):
                capabilities.bind_runtime_context(base,lambda scope:{'read_only':value})
        self.assertTrue(capabilities.bind_runtime_context(base,lambda scope:{})['read_only'])

    def test_review_malformed_identity_fails_closed_without_stdio_crash(self):
        from coding_worker_v2.agent import ingress
        original=self.ctx
        self.assertTrue(ingress.qualified(original))
        malformed=[{'command_auth':None},{'command_auth':{'channel':[]}},
            {'command_channels':None},
            {'command_auth':{**original['command_auth'],'principal_id':[]}},
            {'command_auth':{**original['command_auth'],'role':[]}},
            {'command_auth':{**original['command_auth'],'task_ids':None}},
            {'command_auth':{**original['command_auth'],'permissions':[{}]}}]
        for change in malformed:
            with self.subTest(change=list(change)):
                self.ctx={**original,**change}
                self.assertFalse(ingress.qualified(self.ctx))
                listed,called,ping=self.rpc([('tools/list',{}),('tools/call',{'name':'queue_health','arguments':{}}),('ping',{})])
                self.assertEqual(len(listed['result']['tools']),67)
                self.assertTrue(called['result']['isError'])
                self.assertIn('MCP_TOOL_SCOPE_DENIED',called['result']['content'][0]['text'])
                self.assertEqual(ping['result'],{})
        self.ctx=original

    def test_review_context_mapping_fields_reject_nonobjects(self):
        for field in ('command_auth','command_channels','tool_bindings','database_auth',
                      'database_bindings','command_approvals','km_admin_binding','gui_binding'):
            with self.subTest(field=field),self.assertRaisesRegex(
                    tools.ToolError,'MCP_CONTEXT_MAPPING_INVALID'):
                capabilities.bind_runtime_context(self.ctx,lambda base:{field:None})

    def test_concrete_producer_through_existing_main_stdio_and_real_read_handler(self):
        from coding_worker_v2 import pilot_install
        from coding_worker_v2.agent import km_main
        from urllib import request
        origin=capabilities.KMAdminContextProducer.ORIGIN
        auth={**self.ctx['command_auth'],'channel':'ADMIN_V2','method':'SESSION_CSRF',
              'issuer':origin,'permissions':['queue_health']}
        role=self.ctx['command_channels']['CHATGPT']['principals']['fixture-principal']
        payload={'schema':'KMCW_VERIFIED_READ_CONTEXT_V1','execution_authority':False,
            'account_id':self.ctx['account_id'],'mission_id':self.ctx['mission_id'],
            'task_id':self.ctx['task_id'],'command_auth':auth,
            'command_channels':{'ADMIN_V2':{'qualified':True,'method':'SESSION_CSRF',
                'issuer':origin,'principals':{'fixture-principal':role}}},
            'tool_bindings':self.ctx['tool_bindings']}
        responses=[]
        for value in ({'authenticated':True,'role':'OWNER','csrf':'fixture-csrf'},payload):
            response=mock.MagicMock()
            response.__enter__.return_value.read.return_value=json.dumps(value).encode()
            responses.append(response)
        source=io.BytesIO(('\n'.join(map(json.dumps,[
            {'jsonrpc':'2.0','id':1,'method':'initialize'},
            {'jsonrpc':'2.0','id':2,'method':'tools/call',
             'params':{'name':'queue_health','arguments':{}}}]))+'\n').encode())
        sink=io.StringIO();actual_serve=mcp_server.serve
        base={k:v for k,v in self.ctx.items() if k not in
              {'command_auth','command_channels','tool_bindings'}}
        before=base['store'].health()
        with mock.patch.object(request,'build_opener') as opener, \
             mock.patch.object(pilot_install,'load_context',return_value=base), \
             mock.patch.object(mcp_server,'serve',side_effect=lambda ctx, **kw:actual_serve(ctx,source,sink,**kw)):
            opener.return_value.open.side_effect=responses + responses
            self.assertEqual(km_main.main(['pilot','mcp','--config','isolated-fixture'],
                context_provider=capabilities.KMAdminContextProducer('__Host-km_web='+'a'*64)),0)
            self.assertEqual(opener.return_value.open.call_count,4)
        result=json.loads(sink.getvalue().splitlines()[1])['result']
        self.assertFalse(result['isError'])
        self.assertEqual(json.loads(result['content'][0]['text']),before)
        self.assertEqual(base['store'].health(),before)
        audit=base['store'].load_checkpoint('.tool-audit')['data']['events']
        self.assertTrue(any(e.get('tool')=='queue_health' and e.get('ok') is True for e in audit))

    def test_admin_channel_reader_envelope_uses_verified_grant_scope_only(self):
        from coding_worker_v2.agent.ingress import digest,idempotency_key
        origin=capabilities.KMAdminContextProducer.ORIGIN
        auth={**self.ctx['command_auth'],'channel':'ADMIN_V2','method':'SESSION_CSRF',
              'issuer':origin,'permissions':['kmcw.context']}
        role={**self.ctx['command_channels']['CHATGPT']['principals']['fixture-principal'],
              'permissions':['kmcw.context']}
        scope={'plan_id':'fixture-plan','allowed_paths':['fixture.py'],'payload':{}}
        delegation={'schema':'KMCW_VERIFIED_READ_CONTEXT_V1','execution_authority':False,
            'account_id':self.ctx['account_id'],'mission_id':self.ctx['mission_id'],
            'task_id':self.ctx['task_id'],'command_auth':auth,
            'command_channels':{'ADMIN_V2':{'qualified':True,'method':'SESSION_CSRF',
                'issuer':origin,'principals':{'fixture-principal':role}}},
            'tool_bindings':{'kmcw.context':{**self.binding,
                'arguments':{'command':{'scope':scope}}}}}
        producer=capabilities.KMAdminContextProducer('__Host-km_web='+'a'*64)
        with mock.patch.object(producer,'_read',side_effect=[
                {'authenticated':True,'role':'OWNER','csrf':'fixture'},delegation]):
            supplied=producer(self.ctx)
        envelope=supplied['tool_bindings']['kmcw.context']['arguments']['command']
        self.assertEqual(envelope['scope'],scope)
        self.assertEqual(envelope['principal_id'],'fixture-principal')
        self.assertEqual(envelope['source_hash'],self.binding['source_sha'])
        self.assertEqual(envelope['idempotency_key'],idempotency_key(envelope))
        self.assertEqual(supplied['command_auth']['body_sha256'],digest(envelope))
        self.assertTrue(supplied['read_only'])

    def test_admin_producer_transport_and_exact_missing_policy_error(self):
        from urllib import request, error
        producer=capabilities.KMAdminContextProducer('__Host-km_web='+'a'*64)
        response=mock.MagicMock()
        response.__enter__.return_value.read.return_value=b'{"authenticated":true}'
        with mock.patch.object(request,'build_opener') as opener:
            opener.return_value.open.return_value=response
            producer._read('/api/session')
            sent=opener.return_value.open.call_args.args[0]
            self.assertEqual(sent.get_method(),'GET')
            self.assertEqual(sent.full_url,'https://ai.knowingmind.app/api/session')
            producer._read('/api/kmcw/context',csrf='fixture-csrf')
            sent=opener.return_value.open.call_args.args[0]
            self.assertEqual(sent.get_method(),'POST')
            self.assertEqual(sent.get_header('Origin'),producer.ORIGIN)
            self.assertIsNone(sent.get_header('Authorization'))
            opener.return_value.open.side_effect=error.HTTPError(sent.full_url,503,'',{},
                io.BytesIO(b'{"error":"KMCW_DELEGATION_POLICY_NOT_CONFIGURED"}'))
            with self.assertRaises(tools.ToolError) as caught:
                producer._read('/api/kmcw/context',csrf='fixture-csrf')
            self.assertEqual(caught.exception.detail,'503:KMCW_DELEGATION_POLICY_NOT_CONFIGURED')

    def test_concrete_admin_producer_uses_verified_session_and_explicit_delegation(self):
        from unittest.mock import call
        origin=capabilities.KMAdminContextProducer.ORIGIN
        auth={**self.ctx['command_auth'],'channel':'ADMIN_V2','method':'SESSION_CSRF','issuer':origin}
        role=self.ctx['command_channels']['CHATGPT']['principals']['fixture-principal']
        delegation={'schema':'KMCW_VERIFIED_READ_CONTEXT_V1','execution_authority':False,
            'account_id':self.ctx['account_id'],'mission_id':self.ctx['mission_id'],
            'task_id':self.ctx['task_id'],'command_auth':auth,
            'command_channels':{'ADMIN_V2':{'qualified':True,'method':'SESSION_CSRF',
                'issuer':origin,'principals':{'fixture-principal':role}}},
            'tool_bindings':self.ctx['tool_bindings']}
        auth['permissions']=['queue_health'];role={**role,'permissions':['queue_health']}
        delegation['command_channels']['ADMIN_V2']['principals']['fixture-principal']=role
        producer=capabilities.KMAdminContextProducer('__Host-km_web='+'a'*64)
        with mock.patch.object(producer,'_read',side_effect=[
                {'authenticated':True,'role':'OWNER','csrf':'fixture-csrf'},delegation]) as reads:
            supplied=producer(self.ctx)
        self.assertEqual(reads.call_args_list,[call('/api/session'),
            call('/api/kmcw/context',csrf='fixture-csrf')])
        self.assertTrue(supplied['read_only'])
        ctx=capabilities.bind_runtime_context(self.ctx,lambda base:supplied)
        self.assertEqual(mcp_server._call(self.reg,{'name':'queue_health','arguments':{}},ctx),
            {'inbox':0,'doing':0,'done':0,'failed':0})

    def test_concrete_admin_producer_role_without_delegation_never_becomes_grant(self):
        producer=capabilities.KMAdminContextProducer('__Host-km_web='+'a'*64)
        for session, second in (({'authenticated':True,'role':'USER','csrf':'x'},None),
                ({'authenticated':True,'role':'OWNER','csrf':'x'},{'authenticated':True,'role':'OWNER','account_hint':'123456'})):
            with mock.patch.object(producer,'_read',side_effect=[session,second]):
                with self.assertRaises(tools.ToolError):producer(self.ctx)

    def test_admin_backend_read_health_requires_its_own_contract(self):
        from datetime import datetime,timezone
        self.admin_fixture()
        response=mock.MagicMock()
        value={'schema':'KM_AI_ADMIN_BACKEND_READ_HEALTH_V1','observed_at':datetime.now(timezone.utc).isoformat(),
            'status':'READ_VERIFIED','backend_read_verified':True,'execution_authority':False}
        response.__enter__.return_value.read.return_value=json.dumps(value).encode()
        with mock.patch.object(tools.request,'build_opener') as opener:
            opener.return_value.open.return_value=response
            result=mcp_server._call(self.reg,{'name':'kcc.d1.health','arguments':{}},self.ctx)
            self.assertEqual(opener.return_value.open.call_args.args[0].full_url,
                'https://fixture.invalid/v1/control/backend-read-health')
            self.assertEqual(result['status'],'READ_VERIFIED')
            self.assertFalse(result['execution_authority'])
            value.pop('backend_read_verified')
            response.__enter__.return_value.read.return_value=json.dumps(value).encode()
            with self.assertRaisesRegex(tools.ToolError,'KM_ADMIN_BACKEND_HEALTH_CONTRACT_INVALID'):
                mcp_server._call(self.reg,{'name':'kcc.d1.health','arguments':{}},self.ctx)

    def test_runtime_handoff_preserves_resources_and_rejects_scope_changes(self):
        base = {'store': self.ctx['store'], 'workspace': self.ctx['workspace'],
                'account_id': 'fixture-account', 'read_only': True}
        self.assertIs(capabilities.bind_runtime_context(base, None), base)
        for supplied, error in (({'store': object()}, 'MCP_CONTEXT_FIELDS_DENIED'),
                                ({'account_id':'other'}, 'MCP_CONTEXT_ACCOUNT_MISMATCH'),
                                ({'read_only':False}, 'MCP_CONTEXT_READONLY_DOWNGRADE_DENIED')):
            with self.assertRaisesRegex(tools.ToolError, error):
                capabilities.bind_runtime_context(base, lambda scope: supplied)
        with self.assertRaisesRegex(tools.ToolError, 'MCP_CONTEXT_PRODUCER_REQUIRED'):
            capabilities.bind_runtime_context(base, {'authenticated':True})
        result = capabilities.bind_runtime_context(base, lambda scope: {'command_auth':{}})
        self.assertIs(result['store'], base['store'])
        self.assertFalse(capabilities.authorized(
            {'name':'queue_health','permission':'read','required_permissions':[]},result,{}))

    def test_existing_pilot_entrypoint_receives_producer_then_scoped_stdio(self):
        import argparse
        from coding_worker_v2 import pilot_install
        from coding_worker_v2.agent import km_main
        base = {k:v for k,v in self.ctx.items() if k not in
                {'command_auth','command_channels','tool_bindings'}}
        supplied = {k:self.ctx[k] for k in ('command_auth','command_channels','tool_bindings','task_id','mission_id')}
        request = '\n'.join(json.dumps(x) for x in [
            {'jsonrpc':'2.0','id':1,'method':'initialize'},
            {'jsonrpc':'2.0','id':2,'method':'tools/call',
             'params':{'name':'queue_health','arguments':{}}}])+'\n'
        sink = io.StringIO()
        actual_serve = mcp_server.serve
        def transport(ctx, **kwargs):
            self.assertIs(ctx['store'],base['store'])
            self.assertIs(ctx['command_auth'],supplied['command_auth'])
            return actual_serve(ctx,io.BytesIO(request.encode()),sink,**kwargs)
        with mock.patch.object(pilot_install,'load_context',return_value=base), \
             mock.patch.object(mcp_server,'serve',side_effect=transport):
            self.assertEqual(km_main.main(['pilot','mcp','--config','fixture-only'],
                context_provider=lambda scope:supplied),0)
        called = json.loads(sink.getvalue().splitlines()[1])['result']
        self.assertFalse(called['isError'])
        self.assertEqual(json.loads(called['content'][0]['text']),
                         {'inbox':0,'doing':0,'done':0,'failed':0})

    def test_source_guard_precedes_runtime_producer(self):
        from coding_worker_v2 import pilot_install
        from coding_worker_v2.agent import km_main
        producer = mock.Mock()
        with mock.patch.object(pilot_install,'load_context',
                side_effect=tools.ToolError('PILOT_INSTALLED_SOURCE_CHANGED')), \
             mock.patch('builtins.print'):
            self.assertEqual(km_main.main(['pilot','mcp','--config','fixture-only'],
                context_provider=producer),1)
        producer.assert_not_called()

    def admin_fixture(self):
        names = ['kcc.d1.' + k for k in ('health', 'task_read', 'mission_read', 'node_read')]
        self.ctx['command_auth']['permissions'].extend(names)
        for name in names:
            kind = name.split('.')[-1]
            key = {'task_read': 'task_id', 'mission_read': 'mission_id', 'node_read': 'node_id'}.get(kind)
            self.ctx['tool_bindings'][name] = {**self.binding,
                'arguments': {key: 'fixture-id'} if key else {}}
        self.ctx['km_admin_binding'] = {'qualified': True,
            'origin': 'https://fixture.invalid', 'session_cookie': '__Host-km_web=' + 'a'*64}
        self.ctx['tool_registry'] = self.reg

    def test_km_admin_task_projection_matches_pinned_backend_missions(self):
        from datetime import datetime, timezone
        self.admin_fixture()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'schema':'DHAMMA_CTRL16_BACKOFFICE_OVERVIEW_V1',
            'observed_at':datetime.now(timezone.utc).isoformat(),
            'projection_only':True,'authority':False,
            'missions':[{'mission_id':'fixture-mission','task_id':'fixture-id',
                         'observed_at':'2026-01-01T00:00:00Z'}],
            'runtimes':[]}).encode()
        with mock.patch.object(tools.request,'build_opener') as opener:
            opener.return_value.open.return_value = response
            result = mcp_server._call(self.reg, {'name':'kcc.d1.task_read',
                'arguments':{'task_id':'fixture-id'}}, self.ctx)
        self.assertEqual(result['record']['task_id'],'fixture-id')
        self.assertEqual(result['record']['observed_at'],'2026-01-01T00:00:00Z')
        self.assertEqual(result['row_freshness'],'NOT_INFERRED_FROM_OVERVIEW_TIMESTAMP')
        self.assertFalse(result['execution_authority'])

    def test_km_admin_mapping_owner_admin_and_legacy_ids(self):
        from datetime import datetime, timezone
        self.admin_fixture()
        snapshot = {'observed_at': datetime.now(timezone.utc).isoformat(),
            'projection_only': True, 'authority': False,
            'tasks': [{'task_id': 'fixture-id'}],
            'missions': [{'mission_id': 'fixture-id', 'task_id': 'fixture-id'}],
            'runtimes': [{'node_id': 'fixture-id'}]}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(snapshot).encode()
        with mock.patch.object(tools.request, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            for role in ('OWNER', 'ADMIN'):
                self.ctx['command_auth']['role'] = role
                self.ctx['command_channels']['CHATGPT']['principals']['fixture-principal']['role'] = role
                for kind, key in (('task_read','task_id'),('mission_read','mission_id'),('node_read','node_id')):
                    result = mcp_server._call(self.reg, {'name': 'kcc.d1.'+kind,
                        'arguments': {key: 'fixture-id'}}, self.ctx)
                    self.assertEqual(result['record'][key], 'fixture-id')
                    self.assertFalse(result['execution_authority'])
                    sent = opener.return_value.open.call_args.args[0]
                    self.assertEqual(sent.full_url, 'https://fixture.invalid/v1/control/overview')
                    self.assertEqual(sent.get_method(), 'GET')
                    self.assertIsNone(sent.get_header('Authorization'))
        self.assertEqual(len(self.reg.names()), 67)
        self.assertEqual(self.ctx['store'].health(), {'inbox':0,'doing':0,'done':0,'failed':0})

    def test_km_admin_missing_binding_acl_and_stdio_errors(self):
        self.admin_fixture()
        self.ctx.pop('km_admin_binding')
        with mock.patch.object(tools.request, 'build_opener') as opener:
            with self.assertRaisesRegex(tools.ToolError, 'TOOL_UNAVAILABLE') as exc:
                mcp_server._call(self.reg, {'name':'kcc.d1.health','arguments':{}}, self.ctx)
            self.assertIn('KM_ADMIN_QUALIFIED_ORIGIN_SESSION_BINDING_REQUIRED', exc.exception.detail)
            self.ctx['command_auth']['role'] = 'OBSERVER'
            self.ctx['command_channels']['CHATGPT']['principals']['fixture-principal']['role'] = 'OBSERVER'
            called, ping = self.rpc([('tools/call', {'name':'kcc.d1.task_read',
                'arguments':{'task_id':'fixture-id'}}), ('ping',{})])
            self.assertTrue(called['result']['isError'])
            self.assertEqual(ping['result'], {})
            opener.assert_not_called()
        self.ctx['command_auth']['authenticated'] = False
        with self.assertRaisesRegex(tools.ToolError, 'MCP_TOOL_SCOPE_DENIED'):
            mcp_server._call(self.reg, {'name':'kcc.d1.task_read','arguments':{'task_id':'fixture-id'}}, self.ctx)

    def test_km_admin_projection_rejects_stale_authority_and_ambiguous_rows(self):
        from datetime import datetime, timezone, timedelta
        self.admin_fixture()
        base = {'observed_at': datetime.now(timezone.utc).isoformat(),
                'projection_only': True, 'authority': False,
                'missions': [{'task_id':'fixture-id'}]}
        for change, reason in (({'authority':True}, 'KM_ADMIN_PROJECTION_CONTRACT_INVALID'),
            ({'observed_at':(datetime.now(timezone.utc)-timedelta(seconds=120)).isoformat()}, 'KM_ADMIN_OVERVIEW_STALE'),
            ({'missions':[{'task_id':'fixture-id'},{'task_id':'fixture-id'}]}, 'KM_ADMIN_PROJECTED_RECORD_MISSING_OR_AMBIGUOUS')):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({**base,**change}).encode()
            with mock.patch.object(tools.request, 'build_opener') as opener:
                opener.return_value.open.return_value = response
                with self.assertRaisesRegex(tools.ToolError, reason):
                    mcp_server._call(self.reg, {'name':'kcc.d1.task_read',
                        'arguments':{'task_id':'fixture-id'}}, self.ctx)

    def test_km_admin_network_error_does_not_break_stdio(self):
        from urllib.error import HTTPError
        self.admin_fixture()
        with mock.patch.object(tools.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('https://fixture.invalid',403,'',{},None)
            called,ping = self.rpc([('tools/call', {'name':'kcc.d1.task_read',
                'arguments':{'task_id':'fixture-id'}}), ('ping',{})])
        self.assertIn('KM_ADMIN_AUTH_DENIED', called['result']['content'][0]['text'])
        self.assertEqual(ping['result'], {})

    def test_missing_audit_context_blocks_before_dispatch_and_stdio_survives(self):
        original = self.ctx
        for missing, detail in (('store', 'MCP_AUDIT_STORE_UNAVAILABLE'),
                                ('account_id', 'MCP_AUDIT_ACCOUNT_REQUIRED')):
            with self.subTest(missing=missing):
                self.ctx = {k: v for k, v in original.items() if k != missing}
                self.ctx['tool_registry'] = self.reg
                with mock.patch.object(self.reg, 'call') as dispatch:
                    called, ping = self.rpc([
                        ('tools/call', {'name': 'queue_health', 'arguments': {}}),
                        ('ping', {})])
                dispatch.assert_not_called()
                error = json.loads(called['result']['content'][0]['text'])
                self.assertEqual(error['reason'], 'TOOL_UNAVAILABLE')
                self.assertIn(detail, error['detail'])
                self.assertEqual(ping['result'], {})
        self.ctx = original

    def test_status_dimensions_do_not_promote_fixture_or_block_other_tools(self):
        listed = self.rpc([('tools/list', {})])[0]['result']
        rows = {r['name']: r for r in listed['_meta']['kmcw/capabilities']['tools']}
        self.assertEqual(len(rows), 67)
        self.assertTrue(rows['queue_health']['callable'])
        self.assertFalse(rows['queue_health']['status_flags']['DEFERRED'])
        self.assertTrue(rows['queue_health']['status_flags']['TESTED_ISOLATED'])
        self.assertFalse(rows['queue_health']['status_flags']['LIVE_VERIFIED'])
        self.assertTrue(rows['git_push']['status_flags']['DEFERRED'])
        self.assertTrue(rows['db.postgres.select']['status_flags']['UNKNOWN'])
        for row in rows.values():
            self.assertTrue(row['status_flags']['METADATA_COMPLETE'])
            self.assertFalse(row['status_flags']['LIVE_VERIFIED'])
            self.assertFalse(row['status_flags']['FAILED'])

    def test_metadata_all_registered_sources_and_exact_schemas(self):
        schemas = mcp_server._schemas(self.reg, self.ctx)
        catalog = mcp_server._catalog(self.reg, self.ctx, schemas)
        by_name = {s['name']: s for s in schemas}
        self.assertEqual(len(catalog['tools']), 67)
        self.assertEqual(set(by_name), set(self.reg.names()))
        for row in catalog['tools']:
            self.assertEqual(row['input_schema'], by_name[row['name']]['inputSchema'])
            self.assertTrue(row['backend_ref'])
            self.assertTrue(row['source_ref'])
            self.assertEqual(len(row['source_sha256']), 64)
            self.assertTrue(row['observed_at'])
            self.assertFalse(row['evidence_is_authorization'])

    def test_metadata_related_staging_and_gui_evidence_do_not_grant_access(self):
        ctx = {'workspace': self.ctx['workspace'], 'tool_bindings': {}}
        catalog = mcp_server._catalog(self.reg, ctx, mcp_server._schemas(self.reg, ctx))
        rows = {r['name']: r for r in catalog['tools']}
        self.assertEqual(catalog['callable_count'], 0)
        for name in ('db.postgres.select', 'db.postgres.write', 'db.pgvector.write', 'gui_test'):
            self.assertFalse(rows[name]['backend']['binding_available'])
            self.assertFalse(rows[name]['authorized'])
            self.assertFalse(rows[name]['backend']['related_evidence_is_binding'])
            self.assertTrue(rows[name]['missing_fields'])
        candidate = rows['db.postgres.select']['backend']['related_evidence']
        self.assertEqual(candidate['result']['runtime_identity']['database_identity']['read_only'], 'on')
        self.assertFalse(candidate['write_authorized'])

    def test_metadata_tools_list_carries_current_readiness(self):
        listed = self.rpc([('tools/list', {})])[0]['result']
        rows = {r['name']: r for r in listed['_meta']['kmcw/capabilities']['tools']}
        for item in listed['tools']:
            self.assertEqual(item['_meta']['kmcw/capability'], rows[item['name']])
        self.assertTrue(rows['queue_health']['read_authorized'])
        self.assertFalse(rows['git_push']['write_authorized'])

    def test_metadata_channel_acl_does_not_attest_operation_approval(self):
        self.ctx['command_auth']['permissions'].append('kmcw.mission.submit')
        catalog = mcp_server._catalog(self.reg, self.ctx, mcp_server._schemas(self.reg, self.ctx))
        row = next(r for r in catalog['tools'] if r['name'] == 'kmcw.mission.submit')
        self.assertTrue(row['acl_ready'])
        self.assertIsNone(row['write_authorized'])
        self.assertEqual(row['operation_approval'], 'CALL_TIME_VALIDATION_REQUIRED')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        repo = fixture_repo(root)
        permissions = ['queue_health', 'git_push', 'gui_test']
        self.ctx = {'workspace': repo, 'store': QueueStore(root / 'queue'),
            'account_id': 'fixture-account', 'mission_id': 'fixture-mission', 'task_id': 'fixture-task',
            'clock': lambda: 1000, 'command_auth': {
                'channel': 'CHATGPT', 'method': 'MCP_OAUTH', 'issuer': 'fixture-issuer',
                'authenticated': True, 'role': 'OWNER', 'principal_id': 'fixture-principal',
                'account_id': 'fixture-account', 'mission_id': 'fixture-mission',
                'task_ids': ['fixture-task'], 'permissions': permissions, 'issued_at': 990, 'expires_at': 1050},
            'command_channels': {'CHATGPT': {'qualified': True, 'method': 'MCP_OAUTH',
                'issuer': 'fixture-issuer', 'principals': {'fixture-principal': {
                    'role': 'OWNER', 'account_id': 'fixture-account', 'permissions': permissions,
                    'mission_ids': ['fixture-mission'], 'task_ids': ['fixture-task']}}}},
            'tool_bindings': {}}
        self.binding = {k: self.ctx[k] for k in ('account_id', 'mission_id', 'task_id')}
        self.binding.update(qualified=True, source_sha=current_base_commit(repo), arguments={})
        self.ctx['tool_bindings']['queue_health'] = {**self.binding,
            'resource_ref': 'queue-store:' + str(self.ctx['store'].base.resolve())}
        self.reg = tools.build_default_registry()

    def rpc(self, calls):
        requests = [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}}]
        requests += [dict(jsonrpc='2.0', id=i+1, method=method, params=params)
                     for i, (method, params) in enumerate(calls)]
        sink = io.StringIO()
        mcp_server.serve(self.ctx, io.BytesIO(('\n'.join(map(json.dumps, requests))+'\n').encode()), sink)
        return [json.loads(line) for line in sink.getvalue().splitlines()][1:]

    def test_real_transport_catalog_call_and_denial(self):
        listed, called, denied, scoped = self.rpc([
            ('tools/list', {}), ('tools/call', {'name': 'queue_health', 'arguments': {}}),
            ('tools/call', {'name': 'git_push', 'arguments': {}}),
            ('tools/call', {'name': 'queue_health', 'arguments': {'principal': 'spoof'}})])
        self.assertEqual({x['name'] for x in listed['result']['tools']}, set(self.reg.names()))
        self.assertEqual(len(listed['result']['tools']), 67)
        self.assertFalse(called['result']['isError'])
        self.assertTrue(denied['result']['isError'])
        self.assertIn('MCP_TOOL_SCOPE_DENIED', denied['result']['content'][0]['text'])
        self.assertIn('MCP_TOOL_SCOPE_DENIED', scoped['result']['content'][0]['text'])
        self.assertTrue(self.ctx['store'].load_checkpoint('.tool-audit'))

    def test_full_catalog_accounts_for_disabled_tools_without_exposing_them(self):
        listed = self.rpc([('tools/list', {})])[0]['result']
        catalog = listed['_meta']['kmcw/capabilities']
        rows = {row['name']: row for row in catalog['tools']}
        self.assertEqual(set(rows), set(self.reg.names()))
        self.assertEqual(catalog['registered_count'], len(self.reg.names()))
        self.assertEqual(catalog['exposed_count'], 67)
        self.assertEqual(catalog['callable_count'], 1)
        self.assertTrue(rows['queue_health']['callable'])
        self.assertEqual(rows['queue_health']['policy_decision'], 'CALL_TIME_VALIDATION_REQUIRED')
        self.assertTrue(rows['git_push']['advertised'])
        self.assertFalse(rows['git_push']['authorized'])
        self.assertFalse(rows['git_push']['callable'])
        self.assertTrue(rows['git_push']['blocked_reason'])
        for spec in self.reg.command_contract()['commands']:
            for key in ('input_schema', 'output_schema', 'permission', 'required_permissions'):
                expected = (next(s['inputSchema'] for s in listed['tools']
                                 if s['name'] == spec['name'])
                            if key == 'input_schema' else spec[key])
                self.assertEqual(rows[spec['name']][key], expected)

    def test_catalog_revocation_and_readonly_are_not_call_grants(self):
        self.ctx['command_auth']['permissions'] = []
        listed, denied = self.rpc([
            ('tools/list', {}), ('tools/call', {'name': 'queue_health', 'arguments': {}})])
        catalog = listed['result']['_meta']['kmcw/capabilities']
        self.assertEqual(catalog['callable_count'], 0)
        self.assertEqual(catalog['registered_count'], len(self.reg.names()))
        self.assertTrue(denied['result']['isError'])
        self.ctx['read_only'] = True
        local = mcp_server._catalog(self.reg, self.ctx, mcp_server._schemas(self.reg, self.ctx))
        self.assertFalse(any(row['callable'] for row in local['tools'] if row['permission'] != 'read'))

    def test_per_call_expiry_revocation_scope_and_availability(self):
        for change in ('expiry', 'acl', 'mission', 'source', 'availability'):
            with self.subTest(change=change):
                ctx = dict(self.ctx)
                ctx['command_auth'] = dict(self.ctx['command_auth'])
                ctx['tool_bindings'] = {'queue_health': dict(self.binding)}
                if change == 'expiry': ctx['clock'] = lambda: 1100
                if change == 'acl': ctx['command_auth']['permissions'] = []
                if change == 'mission': ctx['mission_id'] = 'other'
                if change == 'source': ctx['tool_bindings']['queue_health']['source_sha'] = 'a'*40
                if change == 'availability': self.reg._health['queue_health'] = lambda c: (False, 'gone')
                with self.assertRaises(tools.ToolError):
                    mcp_server._call(self.reg, {'name': 'queue_health', 'arguments': {}}, ctx)

    def test_pilot_preserved_and_mutations_not_auto_exposed(self):
        from coding_worker_v2.agent.pilot import TOOLS
        self.assertTrue(set(TOOLS).issubset({s['name'] for s in mcp_server._schemas(self.reg)}))
        self.ctx['tool_bindings']['git_push'] = dict(self.binding)
        self.ctx['database_auth'] = {'authenticated': False}
        names = {s['name'] for s in mcp_server._schemas(self.reg, self.ctx)}
        self.assertIn('git_push', names)
        self.assertTrue(any(n.startswith('db.') for n in names))
        self.assertIn('gui_test', names)

    def test_mutation_requires_verified_scoped_approval(self):
        from coding_worker_v2.agent.ingress import digest
        spec = next(s for s in self.reg.command_contract()['commands'] if s['name'] == 'git_push')
        args = {'repo': str(self.ctx['workspace']), 'branch': 'fixture'}
        self.ctx['tool_bindings']['git_push'] = {**self.binding, 'arguments': args,
            'write_authorized': True, 'approval_ref': 'fixture-approval'}
        self.assertFalse(capabilities.authorized(spec, self.ctx, args))
        self.ctx['command_approvals'] = {'fixture-approval': {
            'verified': True, 'readback_confirmed': True, 'principal_id': 'fixture-principal',
            'mission_id': self.ctx['mission_id'], 'task_id': self.ctx['task_id'],
            'source_hash': self.binding['source_sha'], 'action': 'GIT_PUSH',
            'scope_sha256': digest(args), 'evidence_ref': 'fixture-only',
            'issued_at': 990, 'expires_at': 1050}}
        self.assertTrue(capabilities.authorized(spec, self.ctx, args))
        # Explicit destructive authorization remains a separate registry gate.
        with self.assertRaisesRegex(tools.ToolError, 'PERMISSION_DENIED'):
            self.reg.call('git_push', args, self.ctx, mcp=True)
        self.ctx['command_approvals']['fixture-approval']['readback_confirmed'] = False
        self.assertFalse(capabilities.authorized(spec, self.ctx, args))

    def test_unavailable_reason_and_pilot_contracts(self):
        from coding_worker_v2.agent.pilot import TOOLS
        schemas = {s['name']: s for s in mcp_server._schemas(self.reg, self.ctx)}
        for spec in self.reg.command_contract()['commands']:
            if spec['name'] in TOOLS:
                self.assertEqual(schemas[spec['name']]['inputSchema'], spec['input_schema'])
        self.ctx['tool_registry'] = self.reg
        self.reg._health['queue_health'] = lambda c: (False, 'EXACT_BACKEND_REASON')
        response = self.rpc([('tools/call', {'name': 'queue_health', 'arguments': {}})])[0]
        error = json.loads(response['result']['content'][0]['text'])
        self.assertEqual(error['reason'], 'TOOL_UNAVAILABLE')
        self.assertEqual(error['detail'], 'queue_health EXACT_BACKEND_REASON')
        audit = self.ctx['store'].load_checkpoint('.tool-audit')['data']['events']
        self.assertEqual(audit[-1]['error_class'], 'TOOL_UNAVAILABLE')

    def gui(self):
        root = Path(self.tmp.name)
        image = root / 'existing-tester.png'
        image.write_bytes(b'fixture screenshot')
        path = root / 'existing-tester.json'
        data = {'schema': 'KMCW_GUI_EVIDENCE_V1', 'task_id': self.ctx['task_id'],
            'mission_id': self.ctx['mission_id'], 'screenshot': str(image),
            'screenshot_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
            'status': 'FIXTURE_ONLY', 'request_id': 'fixture-request'}
        self.ctx['gui_binding'] = {**self.binding, 'evidence_path': str(path),
            'screenshot_path': str(image), 'scope': {'url': 'fixture-only'}}
        def runner(**kw):
            path.write_text(json.dumps(data))
        self.ctx['gui_binding']['run'] = mock.Mock(side_effect=runner)
        self.ctx['tool_bindings']['gui_test'] = {**self.binding,
            'arguments': {'request_id': 'fixture-request'}, 'write_authorized': True}
        from coding_worker_v2.agent.ingress import digest
        data.update(scope_sha256=digest(self.ctx['gui_binding']['scope']),
                    source_sha=self.binding['source_sha'])
        self.ctx['gui_binding']['approval_ref'] = 'fixture-approval'
        self.ctx['command_approvals'] = {'fixture-approval': {
            'verified': True, 'readback_confirmed': True, 'principal_id': 'fixture-principal',
            'mission_id': self.ctx['mission_id'], 'task_id': self.ctx['task_id'],
            'source_hash': self.binding['source_sha'], 'action': 'GUI_TEST',
            'scope_sha256': digest(self.ctx['gui_binding']['scope']),
            'evidence_ref': 'fixture-only', 'issued_at': 990, 'expires_at': 1050}}
        return path, image, data

    def test_gui_existing_runner_dedup_and_interrupted_readback(self):
        path, image, data = self.gui()
        args = {'name': 'gui_test', 'arguments': {'request_id': 'fixture-request'}}
        original = self.ctx['store'].save_checkpoint
        def interrupt(key, stage, payload):
            if stage == 'gui_observed': raise OSError('recoverable persistence failure')
            return original(key, stage, payload)
        with mock.patch.object(self.ctx['store'], 'save_checkpoint', side_effect=interrupt):
            with self.assertRaises(tools.ToolError): mcp_server._call(self.reg, args, self.ctx)
        result = mcp_server._call(self.reg, args, self.ctx)
        self.assertEqual(result['delivery'], 'DUPLICATE_IGNORED')
        self.assertTrue(mcp_server._call(self.reg, args, self.ctx)['duplicate'])
        self.ctx['gui_binding']['run'].assert_called_once()
        events = self.ctx['store'].read_events(self.ctx['task_id'])
        self.assertEqual(events['event_count'], 1)
        self.assertTrue(events['chain_intact'])
        self.assertFalse(result['independent_verified'])

    def test_gui_ambiguous_action_and_tampered_evidence_fail_closed(self):
        path, image, data = self.gui()
        self.ctx['gui_binding']['run'].side_effect = OSError('unknown browser outcome')
        args = {'name': 'gui_test', 'arguments': {'request_id': 'fixture-request'}}
        for _ in range(2):
            with self.assertRaises(tools.ToolError): mcp_server._call(self.reg, args, self.ctx)
        self.ctx['gui_binding']['run'].assert_called_once()
        path.write_text(json.dumps(data))
        image.write_bytes(b'tampered')
        with self.assertRaisesRegex(tools.ToolError, 'HASH_MISMATCH'):
            capabilities.ingest_gui(self.ctx, self.ctx['task_id'])

    def test_reconcile_ingests_existing_evidence_after_missing_file_without_dispatch(self):
        from coding_worker_v2.tests.test_unified_continuation import UnifiedContinuationTest
        from coding_worker_v2.agent import executor
        fixture = UnifiedContinuationTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = Path(fixture.tmp.name)
        task_id = executor.namespaced_id(fixture.plan, 't1')
        screenshot = root / 'tester.png'
        screenshot.write_bytes(b'fixture')
        path = root / 'tester.json'
        fixture.ctx['gui_binding'] = {'qualified': True, 'account_id': fixture.ctx['account_id'],
            'mission_id': fixture.plan.mission_id, 'task_id': task_id,
            'evidence_path': str(path), 'screenshot_path': str(screenshot)}
        fixture.ctx['execution_task_ids'] = [task_id]
        with mock.patch.object(executor, 'execute_plan', side_effect=AssertionError('dispatch forbidden')):
            first = executor.reconcile_once(fixture.plan, fixture.ctx)
            self.assertIn('blocked', first['gui_evidence'])
            path.write_text(json.dumps({'schema': 'KMCW_GUI_EVIDENCE_V1', 'task_id': task_id,
                'mission_id': fixture.plan.mission_id, 'screenshot': str(screenshot),
                'screenshot_sha256': hashlib.sha256(screenshot.read_bytes()).hexdigest(),
                'status': 'FIXTURE_ONLY'}))
            recovered = executor.reconcile_once(fixture.plan, fixture.ctx)
            replay = executor.reconcile_once(fixture.plan, fixture.ctx)
        self.assertEqual(recovered['gui_evidence']['delivery'], 'APPENDED')
        self.assertEqual(replay['gui_evidence']['delivery'], 'DUPLICATE_IGNORED')
        self.assertFalse(recovered['dispatch'])
        events = fixture.ctx['store'].read_events(task_id)
        self.assertEqual(sum(e['step'] == 'gui.evidence' for e in events['events']), 1)
        self.assertTrue(events['chain_intact'])

    def test_argument_binding_preserves_json_types(self):
        spec = {'name': 'queue_health', 'permission': 'read', 'required_permissions': []}
        self.ctx['tool_bindings']['queue_health']['arguments'] = {'nested': [1]}
        self.assertTrue(capabilities.authorized(spec, self.ctx, {'nested': [1]}))
        self.assertFalse(capabilities.authorized(spec, self.ctx, {'nested': [True]}))
        self.assertFalse(capabilities.authorized(spec, self.ctx, {'nested': [1.0]}))

    def test_malformed_gui_artifact_is_rejected_without_task_mutation(self):
        path, image, data = self.gui()
        for value in ([], None, {'schema': 'KMCW_GUI_EVIDENCE_V1',
                'mission_id': self.ctx['mission_id'], 'task_id': self.ctx['task_id'],
                'screenshot': []}):
            path.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(tools.ToolError):
                capabilities.ingest_gui(self.ctx, self.ctx['task_id'])
        self.assertEqual(self.ctx['store'].read_events(self.ctx['task_id'])['event_count'], 0)

    def test_actual_stdio_process_list_authorized_call_and_denial(self):
        import os
        import subprocess
        import sys
        context = {k: v for k, v in self.ctx.items() if k not in {'store', 'clock'}}
        context['queue_path'] = str(self.ctx['store'].base)
        context_file = Path(self.tmp.name) / 'fixture-middleware-context.json'
        context_file.write_text(json.dumps(context))
        program = '''
import json, sys
from pathlib import Path
from coding_worker_v2.agent.mcp_server import serve
from coding_worker_v2.queue_store import QueueStore
ctx = json.loads(Path(sys.argv[1]).read_text())
ctx['store'] = QueueStore(Path(ctx.pop('queue_path')))
ctx['clock'] = lambda: 1000
raise SystemExit(serve(ctx))
'''
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'queue_health', 'arguments': {}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
             'params': {'name': 'git_push', 'arguments': {}}}]
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
        result = subprocess.run([sys.executable, '-c', program, str(context_file)],
            input='\n'.join(map(json.dumps, messages))+'\n', capture_output=True,
            text=True, env=env, timeout=15, check=True)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual({t['name'] for t in responses[1]['result']['tools']}, set(self.reg.names()))
        self.assertEqual(len(responses[1]['result']['tools']), 67)
        self.assertFalse(responses[2]['result']['isError'])
        self.assertTrue(responses[3]['result']['isError'])
        self.assertIn('MCP_TOOL_SCOPE_DENIED', responses[3]['result']['content'][0]['text'])
        self.assertTrue(self.ctx['store'].load_checkpoint('.tool-audit'))

    def test_gui_adapter_reuses_equivalent_but_not_unrelated_inventory(self):
        for name, added in (('browser_interact', False), ('playwright_test', False),
                            ('guid_metadata', True), ('browser_inventory', True)):
            registry = tools.ToolRegistry()
            registry.register(tools.ToolSpec(name, 'fixture'), lambda a, c: {})
            capabilities.register_gui(registry)
            with self.subTest(name=name):
                self.assertEqual('gui_test' in registry.names(), added)
