"""Scoped database tools inside the existing registry; no server or credential issuer.

Bindings, authenticated claims, approved SQL and connection objects are supplied
by trusted middleware, never by tool arguments. Unbound tools fail closed.
"""
from __future__ import annotations
import hashlib
import base64
import json
import math
import re
import time
from datetime import datetime, timezone

ROLES = {'OWNER', 'ORCHESTRATOR', 'EXECUTOR', 'PLANNER', 'INDEPENDENT_QC',
         'READER', 'DATABASE_MAINTAINER'}
READ_ROLES = ROLES
WRITE_ROLES = {'OWNER', 'ORCHESTRATOR', 'EXECUTOR'}
MAINTENANCE_ROLES = {'OWNER', 'DATABASE_MAINTAINER'}
OPS = {
    'd1': ('discover', 'metadata', 'schema', 'select', 'write', 'checkpoint', 'maintenance'),
    'postgres': ('health', 'catalog', 'schema', 'select', 'write', 'checkpoint', 'maintenance'),
    'r2': ('discover', 'list', 'metadata', 'get', 'put_evidence', 'maintenance'),
    'pgvector': ('indexes', 'metadata', 'search', 'write', 'maintenance'),
    'memory': ('read', 'write'),
}
WRITES = {'write', 'checkpoint', 'put_evidence'}
RESULT_SCHEMA = {'type': 'object', 'required': ['target_id', 'principal_id', 'mission_id',
    'task_id', 'source_sha', 'observed_at', 'data', 'evidence_ref'],
    'properties': {k: {'type': 'string'} for k in ('target_id', 'principal_id',
        'mission_id', 'task_id', 'source_sha', 'observed_at', 'evidence_ref')} | {'data': {'type': 'object'}}}


def fail(reason):
    from .tools import ToolError
    raise ToolError(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def command_scope(scope):
    """Stable command identity; a renewed fence never means a second command."""
    return {k: v for k, v in scope.items() if k != 'fencing_token'}


def authorize(ctx, system, op, target):
    auth = ctx.get('database_auth', {})
    binding = ctx.get('database_bindings', {}).get(target, {})
    permission = f'db.{system}.{op}'
    now = ctx.get('clock', time.time)()
    if (auth.get('authenticated') is not True or auth.get('role') not in ROLES
            or not isinstance(auth.get('principal_id'), str) or not auth['principal_id']
            or any(type(auth.get(k)) not in (int, float) or not math.isfinite(auth[k])
                   for k in ('issued_at', 'expires_at'))
            or not auth['issued_at'] <= now < auth['expires_at'] <= auth['issued_at'] + 60):
        fail('DB_AUTHENTICATION_REQUIRED')
    if (binding.get('qualified') is not True or binding.get('system') != system
            or binding.get('target_id') != target or not binding.get('authority_ref')
            or not callable(binding.get('audit')) or binding.get('adapter') is None):
        fail('DB_ADAPTER_OR_AUTHORITY_MISSING')
    grant = binding.get('principals', {}).get(auth['principal_id'], {})
    if (permission not in auth.get('permissions', []) or permission not in grant.get('permissions', [])
            or auth.get('role') != grant.get('role')
            or not binding.get('issuer') or auth.get('issuer') != binding['issuer']
            or not binding.get('audience') or auth.get('audience') != binding['audience']
            or any(not ctx.get(k) or auth.get(k) != ctx[k] for k in ('mission_id', 'task_id', 'source_sha'))
            or ctx['mission_id'] not in grant.get('mission_ids', [])
            or ctx['task_id'] not in grant.get('task_ids', [])
            or ctx['source_sha'] not in grant.get('source_shas', [])):
        fail('DB_SCOPE_OR_ACL_DENIED')
    if op in WRITES and (auth['role'] not in WRITE_ROLES or binding.get('write_authorized') is not True):
        fail('DB_WRITE_DENIED')
    if op == 'maintenance' and (auth['role'] not in MAINTENANCE_ROLES
                               or binding.get('maintenance_authorized') is not True):
        fail('DB_MAINTENANCE_DENIED')
    return auth, binding, grant


def health(ctx, system, op):
    # Local qualification only: discovery does not probe or mutate remote stores.
    for target in ctx.get('database_bindings', {}):
        try:
            _, binding, grant = authorize(ctx, system, op, target)
            if not any(binding.get('operations', {}).get(name, {}).get('action') == op
                       for name in grant.get('operations', [])):
                continue
            if op in WRITES or op == 'maintenance':
                if ctx.get('read_only'):
                    continue
                handler = 'maintenance_transaction' if op == 'maintenance' else 'transaction'
                if (not callable(binding.get(handler))
                        or not callable(binding.get('validate_lease'))
                        or not binding.get('lease', {}).get('fencing_token')):
                    continue
            return True, 'QUALIFIED_BINDING_REMOTE_HEALTH_NOT_PROBED'
        except Exception:
            pass
    return False, 'DB_ADAPTER_OR_AUTHORITY_MISSING'


def invoke(system, op, args, ctx):
    auth, binding, grant = authorize(ctx, system, op, args['target_id'])
    params = args.get('params', {})
    if not isinstance(params, dict) or len(json.dumps(params, allow_nan=False).encode()) > 65536:
        fail('DB_INPUT_LIMIT')
    operation = args['operation']
    contract = binding.get('operations', {}).get(operation, {})
    if (contract.get('action') != op or operation not in grant.get('operations', [])
            or not isinstance(contract.get('parameters'), dict)
            or set(params) != set(contract['parameters'])):
        fail('DB_OPERATION_NOT_ALLOWLISTED')
    types = {'str': str, 'int': int, 'float': float, 'list': list, 'dict': dict, 'bool': bool}
    for key, kind in contract['parameters'].items():
        if type(params[key]) is not types.get(kind):
            fail('DB_PARAMETER_TYPE')
    limit = args.get('limit', min(100, contract.get('max_rows', 100)))
    if type(limit) is not int or not 1 <= limit <= min(100, contract.get('max_rows', 100)):
        fail('DB_ROW_LIMIT')
    if system == 'pgvector' and op == 'search':
        vector = params.get('vector')
        dim = contract.get('dimensions')
        if (type(dim) is not int or not 1 <= dim <= 16000 or not isinstance(vector, list)
                or len(vector) != dim or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
                or not contract.get('index_ref') or not contract.get('provenance_ref')):
            fail('DB_VECTOR_METADATA_REQUIRED')
    scope = {k: ctx[k] for k in ('mission_id', 'task_id', 'source_sha')}
    scope.update(principal_id=auth['principal_id'], target_id=args['target_id'])
    write = op in WRITES or op == 'maintenance'
    if write:
        key = args.get('idempotency_key', '')
        if not re.fullmatch(r'[0-9a-f]{64}', key):
            fail('DB_IDEMPOTENCY_REQUIRED')
        lease = binding.get('lease', {})
        if (not callable(binding.get('validate_lease')) or not binding['validate_lease'](scope, lease)
                or not lease.get('fencing_token')):
            fail('DB_LEASE_OR_FENCING_REQUIRED')
        scope.update(idempotency_key=key, fencing_token=lease['fencing_token'])
        # Existing transaction contract must atomically enforce idempotency,
        # scope, fencing, exact readback and durable audit; no new ledger here.
        tx = binding.get('maintenance_transaction' if op == 'maintenance' else 'transaction')
        if not callable(tx):
            fail('DB_ATOMIC_WRITER_UNAVAILABLE')
    audit = binding['audit']
    request_hash = digest({'system': system, 'op': op, 'operation': operation,
                           'params': params, 'scope': command_scope(scope), 'limit': limit})
    pre = audit({'phase': 'attempt', 'request_sha256': request_hash, **scope})
    if not isinstance(pre, str) or not pre:
        fail('DB_DURABLE_AUDIT_REQUIRED')
    try:
        if write:
            result = tx(operation=operation, contract=contract, params=dict(params),
                        scope=dict(scope), limit=limit, request_sha256=request_hash)
            if (not isinstance(result, dict) or result.get('request_sha256') != request_hash
                    or result.get('readback_verified') is not True or not result.get('checkpoint_ref')
                    or not isinstance(result.get('scope'), dict)
                    or command_scope(result['scope']) != command_scope(scope)
                    or not result['scope'].get('fencing_token') or not result.get('audit_ref')):
                fail('DB_WRITE_AMBIGUOUS_DO_NOT_RETRY')
        else:
            result = binding['adapter'].read(operation, contract, dict(params), dict(scope), limit)
        if not isinstance(result, dict) or len(json.dumps(result, allow_nan=False).encode()) > 1048576:
            fail('DB_WRITE_AMBIGUOUS_DO_NOT_RETRY' if write else 'DB_RESULT_LIMIT_OR_TYPE')
        ref = audit({'phase': 'result', 'request_sha256': request_hash,
                     'result_sha256': digest(result), **scope})
        if not isinstance(ref, str) or not ref:
            fail('DB_AUDIT_UNCONFIRMED_DO_NOT_RETRY' if write else 'DB_AUDIT_UNCONFIRMED')
    except Exception:
        # No exception messages, SQL, row values or secrets in audit.
        audit({'phase': 'failed_or_ambiguous', 'request_sha256': request_hash, **scope})
        raise
    return {**{k: v for k, v in scope.items() if k not in ('idempotency_key', 'fencing_token')},
            'observed_at': datetime.now(timezone.utc).isoformat(), 'data': result, 'evidence_ref': ref}


class DhammaMemoryReadAdapter:
    """Reuse an existing authenticated Dhamma callable for bounded retrieval.

    The callable owns transport credentials. This adapter neither creates a
    connection nor grants a principal; invoke() still requires the original
    qualified database binding, task ACL and durable audit. Returned memory
    remains source data, including its original proposal/verification status.
    """
    def __init__(self, call_tool):
        if not callable(call_tool):
            fail('DB_MEMORY_CLIENT_REQUIRED')
        self.call_tool = call_tool

    def read(self, operation, contract, params, scope, limit):
        tool = contract.get('tool')
        if (contract.get('action') != 'read'
                or tool not in {'memory.get_context', 'memory.search'}
                or type(limit) is not int or not 1 <= limit <= 20
                or not isinstance(params, dict)):
            fail('DB_MEMORY_READ_CONTRACT_REQUIRED')
        expected = {'query'} if tool == 'memory.search' else set()
        if set(params) != expected:
            fail('DB_MEMORY_ARGUMENT_SCOPE_DENIED')
        if tool == 'memory.search' and (not isinstance(params['query'], str)
                or not params['query'].strip() or len(params['query']) > 4000):
            fail('DB_MEMORY_ARGUMENT_SCOPE_DENIED')
        # Never send caller-selected account/user IDs to owner-scoped V4 tools.
        value = self.call_tool(tool, {**params, 'limit': limit})
        schema = ('DHAMMA_MEMORY_SEARCH_V1' if tool == 'memory.search'
                  else 'DHAMMA_MEMORY_CONTEXT_V1')
        if (not isinstance(value, dict) or value.get('schema') != schema
                or not isinstance(value.get('items'), list)
                or len(value['items']) > limit
                or not isinstance(value.get('decisions'), list)
                or any(not isinstance(row, dict)
                       for row in value['items'] + value['decisions'])):
            fail('DB_MEMORY_READBACK_CONTRACT_INVALID')
        try:
            raw = json.dumps(value, allow_nan=False)
        except (ValueError, TypeError):
            fail('DB_MEMORY_READBACK_CONTRACT_INVALID')
        if len(raw.encode('utf-8')) > 1_048_576:
            fail('DB_RESULT_LIMIT_OR_TYPE')
        return value


class SQLReadAdapter:
    """Real DB-API read-only transaction using an existing PostgreSQL connection.

    Approved SELECTs include scope predicates, timeout and LIMIT bind parameters.
    Role privileges/RLS remain authoritative. Never accepts SQL from tool callers.
    """
    def __init__(self, connection):
        self.connection = connection

    def read(self, operation, contract, params, scope, limit):
        sql = contract.get('sql', '')
        if (not isinstance(sql, str) or not re.match(r'^\s*SELECT\b', sql, re.I)
                or ';' in sql or '--' in sql or '/*' in sql
                or any('%(' + k + ')s' not in sql for k in ('mission_id', 'task_id', 'source_sha', 'limit'))):
            fail('DB_SCOPED_SELECT_REQUIRED')
        # transaction() rolls back on any readback/timeout error and refuses a
        # writer statement even if a configured SELECT calls a volatile function.
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute("SET LOCAL statement_timeout = '5000ms'")
                cur.execute(sql, {**params, **scope, 'limit': limit})
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(limit + 1)
                if len(rows) > limit:
                    fail('DB_QUERY_EXCEEDED_LIMIT')
                return {'rows': [dict(zip(cols, row)) for row in rows],
                        'authority_ref': contract.get('provenance_ref')}


class D1ReadAdapter:
    """Cloudflare D1 query API using an already authenticated HTTP client.

    Client must be preconfigured for the verified account; this adapter never
    creates credentials or discovers an account from environment variables.
    """
    def __init__(self, client, account_id, database_id):
        if not re.fullmatch(r'[0-9a-f]{32}', account_id) or not re.fullmatch(r'[0-9a-f-]{36}', database_id):
            fail('DB_TARGET_ID_INVALID')
        self.client, self.account_id, self.database_id = client, account_id, database_id

    def read(self, operation, contract, params, scope, limit):
        if contract.get('action') in {'discover', 'metadata'}:
            response = self.client.get(f'/accounts/{self.account_id}/d1/database/{self.database_id}', timeout=5)
            response.raise_for_status()
            payload = response.json()
            result = payload.get('result', {})
            if payload.get('success') is not True or result.get('uuid') != self.database_id:
                fail('DB_D1_TARGET_MISMATCH')
            return {'database_id': result['uuid'], 'name': result.get('name'),
                    'version': result.get('version')}
        sql = contract.get('sql', '')
        order = contract.get('bind_order', [])
        if (not re.match(r'^\s*SELECT\b', sql, re.I) or any(x in sql for x in (';', '--', '/*'))
                or not set(('mission_id', 'task_id', 'source_sha', 'limit')).issubset(order)):
            fail('DB_SCOPED_SELECT_REQUIRED')
        values = {**params, **scope, 'limit': limit}
        response = self.client.post(f'/accounts/{self.account_id}/d1/database/{self.database_id}/query',
                                    json={'sql': sql, 'params': [values[k] for k in order]}, timeout=5)
        response.raise_for_status()
        payload = response.json()
        if payload.get('success') is not True or len(payload.get('result', [])) != 1:
            fail('DB_D1_QUERY_FAILED')
        result = payload['result'][0]
        if result.get('success') is not True or result.get('meta', {}).get('changes', 0) != 0:
            fail('DB_READ_MUTATION_OR_FAILURE')
        rows = result.get('results')
        if not isinstance(rows, list) or len(rows) > limit:
            fail('DB_QUERY_EXCEEDED_LIMIT')
        return {'rows': rows, 'authority_ref': contract.get('provenance_ref')}


class PostgresTransactionAdapter:
    """Atomic write/readback/receipt on an existing connection and existing tables.

    A qualified contract supplies scoped parameterized SQL for lease row locking,
    prior receipt lookup, mutation, exact readback, and receipt+audit insert.
    No tables, grants or keys are created. Existing receipt uniqueness constraints
    and locked lease row serialize retries; missing contracts remain blocked.
    """
    def __init__(self, connection):
        self.connection = connection

    def __call__(self, *, operation, contract, params, scope, limit, request_sha256):
        keys = ('guard_sql', 'prior_sql', 'write_sql', 'readback_sql', 'receipt_sql')
        verbs = {'guard_sql': 'SELECT', 'prior_sql': 'SELECT',
                 'write_sql': '(?:INSERT|UPDATE)', 'readback_sql': 'SELECT', 'receipt_sql': 'INSERT'}
        if any(not isinstance(contract.get(k), str) or not contract[k] for k in keys):
            fail('DB_TRANSACTION_CONTRACT_MISSING')
        for key in keys:
            sql = contract[key]
            if (any(x in sql for x in (';', '--', '/*'))
                    or not re.match(r'^\s*' + verbs[key] + r'\b', sql, re.I)
                    or any('%(' + k + ')s' not in sql for k in
                           ('mission_id', 'task_id', 'source_sha'))):
                fail('DB_TRANSACTION_SCOPE_REQUIRED')
        if ('FOR UPDATE' not in contract['guard_sql'].upper()
                or '%(fencing_token)s' not in contract['guard_sql']
                or any('%(idempotency_key)s' not in contract[k]
                       for k in ('prior_sql', 'receipt_sql'))):
            fail('DB_TRANSACTION_FENCING_OR_DEDUP_REQUIRED')
        values = {**params, **scope, 'limit': limit, 'request_sha256': request_sha256}
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '5000ms'")
                cur.execute(contract['guard_sql'], values)
                if cur.fetchone() != (True,):
                    fail('DB_LEASE_OR_FENCING_REQUIRED')
                cur.execute(contract['prior_sql'], values)
                prior = cur.fetchone()
                if prior:
                    receipt = prior[0]
                    if (not isinstance(receipt, dict) or receipt.get('request_sha256') != request_sha256
                            or not isinstance(receipt.get('scope'), dict)
                            or command_scope(receipt['scope']) != command_scope(scope)
                            or not receipt['scope'].get('fencing_token')
                            or receipt.get('readback_verified') is not True
                            or not receipt.get('checkpoint_ref') or not receipt.get('audit_ref')):
                        fail('DB_IDEMPOTENCY_CONFLICT')
                    return receipt
                cur.execute(contract['write_sql'], values)
                if cur.rowcount != 1:
                    fail('DB_WRITE_ROWCOUNT_REQUIRED')
                cur.execute(contract['readback_sql'], values)
                rows = cur.fetchmany(2)
                # Qualified SQL must project a single JSON object containing
                # exact expected fields. Compare before commit, never infer
                # verification from rowcount or an HTTP success response.
                if len(rows) != 1 or rows[0][0] != params.get('expected'):
                    fail('DB_EXACT_READBACK_MISMATCH')
                result = {'request_sha256': request_sha256, 'scope': dict(scope),
                          'readback_verified': True, 'rows': [rows[0][0]],
                          'checkpoint_ref': contract['checkpoint_prefix'] + scope['idempotency_key'],
                          'audit_ref': contract['audit_prefix'] + scope['idempotency_key']}
                cur.execute(contract['receipt_sql'], {**values, 'receipt_json': json.dumps(result)})
                if cur.rowcount != 1:
                    fail('DB_ATOMIC_RECEIPT_REQUIRED')
                cur.execute(contract['prior_sql'], values)
                if cur.fetchone() != (result,):
                    fail('DB_RECEIPT_READBACK_MISMATCH')
                return result


class R2ReadAdapter:
    """Existing S3-compatible R2 client; exact keys/prefixes from trusted contracts."""
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    def read(self, operation, contract, params, scope, limit):
        key = contract.get('key')
        if contract['action'] == 'discover':
            self.client.head_bucket(Bucket=self.bucket)
            return {'bucket': self.bucket, 'scope': 'EXACT_CONFIGURED_BUCKET_ONLY'}
        if contract['action'] == 'list':
            result = self.client.list_objects_v2(Bucket=self.bucket, Prefix=contract['prefix'], MaxKeys=limit)
            return {'objects': [{'key': o['Key'], 'size': o['Size'], 'etag': o.get('ETag')}
                                for o in result.get('Contents', [])], 'truncated': result.get('IsTruncated', False)}
        if contract['action'] == 'metadata':
            r = self.client.head_object(Bucket=self.bucket, Key=key)
            return {'size': r['ContentLength'], 'etag': r.get('ETag'), 'version_id': r.get('VersionId'),
                    'sha256': r.get('Metadata', {}).get('sha256')}
        if contract['action'] != 'get' or not key or not contract.get('sha256'):
            fail('DB_R2_EXACT_EVIDENCE_REQUIRED')
        r = self.client.get_object(Bucket=self.bucket, Key=key)
        body = r['Body']
        try:
            data = body.read(65537)
        finally:
            body.close()
        if len(data) > 65536 or hashlib.sha256(data).hexdigest() != contract['sha256']:
            fail('DB_R2_CONTENT_HASH_OR_LIMIT')
        return {'content_base64': base64.b64encode(data).decode('ascii'), 'sha256': contract['sha256'],
                'version_id': r.get('VersionId'), 'etag': r.get('ETag')}

    def put_immutable(self, key, content, scope, request_sha256):
        """Conditional evidence upload for an existing governed transaction lane.

        This helper never grants lease/fencing authority and is not registered
        as a standalone bypass. A qualified transaction controller must call it.
        Existing keys conflict; no overwrite, deletion or automatic retry.
        """
        if (not isinstance(content, bytes) or len(content) > 65536 or not isinstance(key, str)
                or not key or key.startswith('/') or '..' in key.split('/')):
            fail('DB_R2_EVIDENCE_INPUT_INVALID')
        sha = hashlib.sha256(content).hexdigest()
        metadata = {'sha256': sha, 'request-sha256': request_sha256,
                    'scope-sha256': digest(scope)}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content,
                               IfNoneMatch='*', Metadata=metadata)
        result = self.read('get', {'action': 'get', 'key': key, 'sha256': sha}, {}, scope, 1)
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        if head.get('Metadata') != metadata:
            fail('DB_R2_READBACK_AMBIGUOUS_DO_NOT_RETRY')
        return {**result, 'object_ref': f'r2://{self.bucket}/{key}',
                'request_sha256': request_sha256, 'scope_sha256': digest(scope)}


def register_database_tools(reg):
    from .tools import ToolSpec
    for system, operations in OPS.items():
        for op in operations:
            name = f'db.{system}.{op}'
            write = op in WRITES or op == 'maintenance'
            reg.register(ToolSpec(name, f'scoped existing {system} {op}',
                {'target_id': 'str', 'operation': 'str'},
                {'params': 'dict', 'limit': 'int', **({'idempotency_key': 'str'} if write else {})},
                'write' if write else 'read',
                'existing authorized transaction and durable audit' if write else 'durable access audit only',
                '100 rows; 64KiB input; 1MiB output; approved statements only; no caller SQL; 60s identity TTL',
                RESULT_SCHEMA, (name,), 'existing binding audit sink; intent + hashed result; write requires atomic receipt'),
                lambda a, c, system=system, op=op: invoke(system, op, a, c),
                lambda c, system=system, op=op: health(c, system, op))
