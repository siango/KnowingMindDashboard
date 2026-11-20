"""Isolated database security tests; never live acceptance."""
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from coding_worker_v2.agent import database_tools as db, mcp_server
from coding_worker_v2.agent.tools import build_default_registry, ToolError

class DatabaseToolsTest(unittest.TestCase):
    def test_dhamma_memory_adapter_uses_existing_client_and_scoped_audit(self):
        payload={'schema':'DHAMMA_MEMORY_CONTEXT_V1','items':[
            {'memory_id':'fixture-prior','evidence_status':'PROPOSED'}],'decisions':[]}
        client=Mock(return_value=payload)
        self.binding.update(system='memory',adapter=db.DhammaMemoryReadAdapter(client),
            operations={'get':{'action':'read','parameters':{},'tool':'memory.get_context','max_rows':20}})
        self.grant.update(permissions=['db.memory.read'],operations=['get'])
        self.ctx['database_auth']['permissions']=['db.memory.read']
        result=self.reg.call('db.memory.read',
            {'target_id':'staging','operation':'get','limit':5},self.ctx,mcp=True)
        self.assertIs(result['data'],payload)
        client.assert_called_once_with('memory.get_context',{'limit':5})
        self.assertEqual([e['phase'] for e in self.events],['attempt','result'])
        self.assertEqual(payload['items'][0]['evidence_status'],'PROPOSED')
        self.reg.call('db.memory.read',
            {'target_id':'staging','operation':'get'},self.ctx,mcp=True)
        self.assertEqual(client.call_args.args,('memory.get_context',{'limit':20}))
        self.ctx['database_auth']['authenticated']=False
        with self.assertRaisesRegex(ToolError,'DB_ADAPTER_OR_AUTHORITY_MISSING'):
            self.reg.call('db.memory.read',
                {'target_id':'staging','operation':'get','limit':5},self.ctx,mcp=True)
        self.assertEqual(client.call_count,2)

    def test_dhamma_memory_adapter_rejects_write_identity_override_and_bad_readback(self):
        client=Mock();adapter=db.DhammaMemoryReadAdapter(client)
        for contract,params,limit in [
            ({'action':'write','tool':'memory.propose'},{},5),
            ({'action':'read','tool':'memory.get_context'},{'user_id':'other'},5),
            ({'action':'read','tool':'memory.search'},{'query':''},5),
            ({'action':'read','tool':'memory.get_context'},{},100)]:
            with self.assertRaises(ToolError):adapter.read('get',contract,params,{},limit)
        client.assert_not_called()
        client.return_value={'schema':'DHAMMA_MEMORY_CONTEXT_V1',
                             'items':[{}]*6,'decisions':[]}
        with self.assertRaisesRegex(ToolError,'READBACK_CONTRACT_INVALID'):
            adapter.read('get',{'action':'read','tool':'memory.get_context'},{},{},5)

    def setUp(self):
        self.reg=build_default_registry(); self.events=[]; self.adapter=Mock()
        self.adapter.read.return_value={'rows':[{'value':'retained'}]}
        perms=['db.postgres.select','db.postgres.write','db.postgres.maintenance']
        self.grant={'role':'EXECUTOR','permissions':perms,'mission_ids':['m'],'task_ids':['t'],'source_shas':['s'],'operations':['get','save','maint']}
        self.binding={'qualified':True,'system':'postgres','target_id':'staging','authority_ref':'existing',
          'issuer':'issuer','audience':'aud','principals':{'executor':self.grant},'adapter':self.adapter,
          'audit':self.audit,'write_authorized':True,'operations':{
          'get':{'action':'select','parameters':{}},'save':{'action':'write','parameters':{'expected':'dict'}},
          'maint':{'action':'maintenance','parameters':{}}}}
        self.ctx={'workspace':'/tmp','clock':lambda:100,'mission_id':'m','task_id':'t','source_sha':'s',
          'database_bindings':{'staging':self.binding},'database_auth':{'authenticated':True,'principal_id':'executor',
          'role':'EXECUTOR','issuer':'issuer','audience':'aud','permissions':perms,'mission_id':'m','task_id':'t',
          'source_sha':'s','issued_at':90,'expires_at':120}}
    def audit(self,e):
        self.events.append(e); return 'existing-audit/'+str(len(self.events))
    def call(self,op='select',**extra):
        return self.reg.call('db.postgres.'+op,{'target_id':'staging','operation':'get' if op=='select' else 'save',**extra},self.ctx)
    def test_registry_contract_and_unbound_health(self):
        specs=[s for s in self.reg.command_contract()['commands'] if s['name'].startswith('db.')]
        self.assertEqual(len(specs),sum(map(len,db.OPS.values())))
        self.assertTrue(all(s['required_permissions'] and s['output_schema'] and s['audit'] for s in specs))
        self.assertFalse(db.health({},'postgres','select')[0])
    def test_read_audit_without_values(self):
        self.assertEqual(self.call()['data']['rows'][0]['value'],'retained')
        self.assertEqual([e['phase'] for e in self.events],['attempt','result'])
        self.assertNotIn('retained',json.dumps(self.events))
    def test_caller_sql_and_unknown_operation_denied(self):
        for args in [{'sql':'DELETE'},{'operation':'unknown'},{'params':{'sql':'DELETE'}},{'limit':101},{'limit':True}]:
            with self.assertRaises(ToolError): self.call(**args)
        self.adapter.read.assert_not_called()
    def test_identity_issuer_audience_expiry_and_task_scope(self):
        for key,value in [('authenticated',False),('issuer','other'),('audience','other'),('expires_at',100),('task_id','other'),('source_sha','other')]:
            old=self.ctx['database_auth'][key];self.ctx['database_auth'][key]=value
            with self.subTest(key=key),self.assertRaises(ToolError): self.call()
            self.ctx['database_auth'][key]=old
        self.adapter.read.assert_not_called()
    def test_all_read_roles_and_write_role_isolation(self):
        for role in db.ROLES:
            self.ctx['database_auth']['role']=self.grant['role']=role;self.call()
            if role not in db.WRITE_ROLES:
                with self.assertRaises(ToolError):self.call('write',params={'expected':{}})
    def test_audit_required_before_access(self):
        self.binding['audit']=lambda e:None
        with self.assertRaises(ToolError):self.call()
        self.adapter.read.assert_not_called()
    def test_write_lease_atomic_receipt_and_ambiguity(self):
        args={'params':{'expected':{'value':1}},'idempotency_key':'a'*64}
        with self.assertRaises(ToolError):self.call('write',**args)
        self.binding.update(validate_lease=lambda s,l:True,lease={'fencing_token':'f'})
        with self.assertRaises(ToolError):self.call('write',**args)
        self.binding['transaction']=lambda **kw:{'readback_verified':True}
        with self.assertRaisesRegex(ToolError,'AMBIGUOUS'):self.call('write',**args)
        self.binding['transaction']=lambda **kw:{'request_sha256':kw['request_sha256'],'readback_verified':True,
            'checkpoint_ref':'existing/receipt','scope':kw['scope'],'audit_ref':'existing/audit'}
        self.assertTrue(self.call('write',**args)['data']['readback_verified'])
    def test_maintenance_separate_and_readonly_denies_write(self):
        self.binding['maintenance_authorized']=True
        with self.assertRaises(ToolError):self.call('maintenance',operation='maint',idempotency_key='a'*64)
        self.ctx['read_only']=True
        with self.assertRaisesRegex(ToolError,'PERMISSION_DENIED'):self.call('write')
    def test_mcp_only_qualified_tools_no_legacy_fallback(self):
        names={s['name'] for s in mcp_server._schemas(self.reg,self.ctx)}
        self.assertIn('db.postgres.select',names);self.assertIn('pilot_run',names);self.assertIn('db.r2.get',names)
        self.assertIn('db.postgres.write', names)
        self.ctx['database_auth']['authenticated']=False
        self.assertEqual(len(mcp_server._schemas(self.reg,self.ctx)),67)
        with self.assertRaisesRegex(ToolError, 'MCP_TOOL_SCOPE_DENIED'):
            self.reg.call('pilot_run', {}, self.ctx, mcp=True)
    def test_postgres_readonly_scope_and_bounded_fetch(self):
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value;cur.description=[('value',)];cur.fetchmany.return_value=[(7,)]
        sql='SELECT value FROM existing WHERE m=%(mission_id)s AND t=%(task_id)s AND s=%(source_sha)s LIMIT %(limit)s'
        self.assertEqual(db.SQLReadAdapter(conn).read('get',{'sql':sql},{},{},1)['rows'],[{'value':7}])
        self.assertEqual(cur.execute.call_args_list[0].args[0],'SET TRANSACTION READ ONLY')
        cur.fetchmany.return_value=[(7,),(8,)]
        with self.assertRaises(ToolError):db.SQLReadAdapter(conn).read('get',{'sql':sql},{},{},1)
    def test_d1_parameter_binding_and_zero_mutation(self):
        client=Mock();reply=client.post.return_value
        reply.json.return_value={'success':True,'result':[{'success':True,'results':[],'meta':{'changes':0}}]}
        a=db.D1ReadAdapter(client,'a'*32,'12345678-1234-1234-1234-123456789012')
        c={'sql':'SELECT ?, ?, ? LIMIT ?','bind_order':['mission_id','task_id','source_sha','limit']};scope={'mission_id':'m','task_id':'t','source_sha':'s'}
        a.read('get',c,{},scope,1)
        self.assertEqual(client.post.call_args.kwargs['json']['params'],['m','t','s',1])
        reply.json.return_value['result'][0]['meta']['changes']=1
        with self.assertRaises(ToolError):a.read('get',c,{},scope,1)
    def test_r2_hash_verification_and_stream_closed(self):
        client=Mock();body=Mock();body.read.return_value=b'evidence';client.get_object.return_value={'Body':body,'VersionId':'v'}
        a=db.R2ReadAdapter(client,'existing');c={'action':'get','key':'existing/evidence','sha256':hashlib.sha256(b'evidence').hexdigest()}
        self.assertEqual(a.read('get',c,{},{},1)['version_id'],'v');body.close.assert_called_once()
        c['sha256']='0'*64
        with self.assertRaises(ToolError):a.read('get',c,{},{},1)
    def test_vector_dimension_finite_values_and_index_provenance(self):
        self.binding['system']='pgvector';self.binding['operations']['search']={'action':'search','parameters':{'vector':'list'},'dimensions':2,'index_ref':'existing-index','provenance_ref':'existing-source'}
        self.grant['operations'].append('search');self.grant['permissions'].append('db.pgvector.search')
        for vector in [[1.0],[True,1.0],[1.0,float('nan')]]:
            with self.assertRaises((ToolError,ValueError)):self.reg.call('db.pgvector.search',{'target_id':'staging','operation':'search','params':{'vector':vector}},self.ctx)
        self.reg.call('db.pgvector.search',{'target_id':'staging','operation':'search','params':{'vector':[1.0,2.0]}},self.ctx)


class TransactionTest(unittest.TestCase):
    """Exercise SQL against in-memory SQLite with a DB-API dialect shim.

    This verifies commit/rollback/dedup logic; PostgreSQL acceptance is NOT_RUN.
    """
    def setUp(self):
        import sqlite3
        self.raw=sqlite3.connect(':memory:')
        self.raw.executescript('CREATE TABLE lease(m TEXT,t TEXT,s TEXT,f TEXT);'
          "INSERT INTO lease VALUES('m','t','s','f');"
          'CREATE TABLE records(m TEXT,t TEXT,s TEXT,payload TEXT);'
          'CREATE TABLE receipts(k TEXT PRIMARY KEY,m TEXT,t TEXT,s TEXT,payload TEXT);')
        self.conn=self.Connection(self.raw)
        self.adapter=db.PostgresTransactionAdapter(self.conn)
        self.scope={'mission_id':'m','task_id':'t','source_sha':'s','principal_id':'p','target_id':'existing',
                    'idempotency_key':'a'*64,'fencing_token':'f'}
        where='m=%(mission_id)s AND t=%(task_id)s AND s=%(source_sha)s'
        self.contract={'guard_sql':'SELECT 1=1 FROM lease WHERE '+where+' AND f=%(fencing_token)s FOR UPDATE',
          'prior_sql':'SELECT payload FROM receipts WHERE '+where+' AND k=%(idempotency_key)s',
          'write_sql':'INSERT INTO records(m,t,s,payload) VALUES(%(mission_id)s,%(task_id)s,%(source_sha)s,%(actual)s)',
          'readback_sql':'SELECT payload FROM records WHERE '+where,
          'receipt_sql':'INSERT INTO receipts(k,m,t,s,payload) VALUES(%(idempotency_key)s,%(mission_id)s,%(task_id)s,%(source_sha)s,%(receipt_json)s)',
          'checkpoint_prefix':'existing/checkpoint/','audit_prefix':'existing/audit/'}
    def tearDown(self):self.raw.close()
    class Connection:
        def __init__(self,raw):self.raw=raw
        def transaction(self):return self.raw
        def cursor(self):return TransactionTest.Cursor(self.raw.cursor())
    class Cursor:
        def __init__(self,c):self.c=c
        def __enter__(self):return self
        def __exit__(self,*args):self.c.close()
        @property
        def rowcount(self):return self.c.rowcount
        def execute(self,sql,values=None):
            import re
            if sql.startswith('SET '):return
            sql=re.sub(r'%\((\w+)\)s',r':\1',sql).replace(' FOR UPDATE','')
            vals={k:json.dumps(v) if isinstance(v,dict) else v for k,v in (values or {}).items()}
            self.c.execute(sql,vals)
        def decode(self,row):
            if row and isinstance(row[0],str) and row[0].startswith('{'):return (json.loads(row[0]),)
            return row
        def fetchone(self):return self.decode(self.c.fetchone())
        def fetchmany(self,n):return [self.decode(r) for r in self.c.fetchmany(n)]
    def run_tx(self,expected=None,actual=None,request_hash='hash'):
        return self.adapter(operation='save',contract=self.contract,
          params={'expected':expected or {'value':1},'actual':actual or {'value':1}},
          scope=self.scope,limit=1,request_sha256=request_hash)
    def test_commit_durable_receipt_and_retry_has_no_duplicate_write(self):
        first=self.run_tx();second=self.run_tx()
        self.assertEqual(first,second)
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM receipts').fetchone()[0],1)
    def test_readback_mismatch_rolls_back_without_receipt(self):
        with self.assertRaisesRegex(ToolError,'READBACK_MISMATCH'):self.run_tx(actual={'value':2})
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM records').fetchone()[0],0)
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM receipts').fetchone()[0],0)
    def test_stale_fence_and_conflicting_retry_denied(self):
        self.scope['fencing_token']='stale'
        with self.assertRaisesRegex(ToolError,'FENCING'):self.run_tx()
        self.scope['fencing_token']='f';self.run_tx()
        with self.assertRaisesRegex(ToolError,'CONFLICT'):self.run_tx(request_hash='different')
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)

class R2ImmutableUploadTest(unittest.TestCase):
    def test_conditional_upload_hash_readback_and_conflict_never_overwrites(self):
        client=Mock();body=Mock();body.read.return_value=b'\xffevidence'
        client.get_object.return_value={'Body':body,'VersionId':'v'}
        scope={'mission_id':'m','task_id':'t','source_sha':'s'}
        metadata={'sha256':hashlib.sha256(b'\xffevidence').hexdigest(),'request-sha256':'h','scope-sha256':db.digest(scope)}
        client.head_object.return_value={'Metadata':metadata}
        adapter=db.R2ReadAdapter(client,'existing')
        result=adapter.put_immutable('approved/evidence',b'\xffevidence',scope,'h')
        self.assertEqual(result['sha256'],metadata['sha256'])
        self.assertEqual(client.put_object.call_args.kwargs['IfNoneMatch'],'*')
        client.put_object.side_effect=RuntimeError('precondition failed')
        with self.assertRaises(RuntimeError):adapter.put_immutable('approved/evidence',b'other',scope,'h')
        self.assertEqual(client.get_object.call_count,1)
        self.assertFalse(client.delete_object.called)



class DatabaseRecoveryTest(unittest.TestCase):
    setUp = DatabaseToolsTest.setUp
    audit = DatabaseToolsTest.audit
    call = DatabaseToolsTest.call
    def test_missing_operation_not_advertised_and_readonly_hides_writer(self):
        self.binding['operations'].pop('get')
        self.assertFalse(db.health(self.ctx, 'postgres', 'select')[0])
        self.binding.update(transaction=lambda **kw: {}, validate_lease=lambda s,l:True,
                            lease={'fencing_token':'f'})
        self.assertTrue(db.health(self.ctx, 'postgres', 'write')[0])
        self.ctx['read_only'] = True
        self.assertFalse(db.health(self.ctx, 'postgres', 'write')[0])
    def test_request_identity_survives_authorized_lease_renewal(self):
        receipts=[]; requests=[]
        def transaction(**kw):
            requests.append(kw['request_sha256'])
            if not receipts:
                receipts.append({'request_sha256':kw['request_sha256'],'scope':dict(kw['scope']),
                    'readback_verified':True,'checkpoint_ref':'existing/receipt','audit_ref':'existing/audit'})
            return receipts[0]
        self.binding.update(transaction=transaction,validate_lease=lambda s,l:True,lease={'fencing_token':'f1'})
        args={'params':{'expected':{'value':1}},'idempotency_key':'b'*64}
        first=self.call('write',**args)
        self.binding['lease']['fencing_token']='f2'
        second=self.call('write',**args)
        self.assertEqual(requests[0],requests[1])
        self.assertEqual(first['data'],second['data'])
        self.assertEqual(second['data']['scope']['fencing_token'],'f1')

    def test_maintenance_requires_separate_handler_even_for_owner(self):
        self.ctx['database_auth']['role']=self.grant['role']='OWNER'
        self.binding.update(maintenance_authorized=True,transaction=lambda **kw:{},
                            validate_lease=lambda s,l:True,lease={'fencing_token':'f'})
        self.assertFalse(db.health(self.ctx,'postgres','maintenance')[0])
        with self.assertRaises(ToolError):
            self.call('maintenance',operation='maint',idempotency_key='a'*64)
        self.binding['maintenance_transaction']=lambda **kw:{}
        self.assertTrue(db.health(self.ctx,'postgres','maintenance')[0])

class ReceiptReadbackTest(unittest.TestCase):
    setUp = TransactionTest.setUp
    tearDown = TransactionTest.tearDown
    run_tx = TransactionTest.run_tx
    Connection = TransactionTest.Connection
    def test_receipt_corruption_rolls_back_data_and_receipt(self):
        self.raw.execute("CREATE TRIGGER corrupt_receipt AFTER INSERT ON receipts BEGIN UPDATE receipts SET payload='{}' WHERE k=NEW.k; END")
        with self.assertRaisesRegex(ToolError,'RECEIPT_READBACK_MISMATCH'):self.run_tx()
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM records').fetchone()[0],0)
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM receipts').fetchone()[0],0)

    def test_new_fence_replays_exact_original_receipt_without_second_mutation(self):
        first=self.run_tx()
        self.raw.execute("UPDATE lease SET f='f2'");self.raw.commit()
        self.scope['fencing_token']='f2'
        second=self.run_tx()
        self.assertEqual(first,second)
        self.assertEqual(second['scope']['fencing_token'],'f')
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)

    def test_mutating_receipt_reader_is_rejected_before_transaction(self):
        self.contract['prior_sql']=self.contract['receipt_sql']
        with self.assertRaisesRegex(ToolError,'TRANSACTION_SCOPE'):self.run_tx()
        self.assertEqual(self.raw.execute('SELECT COUNT(*) FROM records').fetchone()[0],0)

if __name__=='__main__':unittest.main()
