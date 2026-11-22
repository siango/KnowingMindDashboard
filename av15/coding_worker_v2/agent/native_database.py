"""Bind the existing owner-only libpq service to an exact, read-only KMCO dataset.

Called only after POSIX UID/private-config/workspace ownership validation.
Never remote Admin authority, Canon authority, arbitrary SQL or caller credentials.
"""
import hashlib
import json
import os
import time

from .database_tools import SQLReadAdapter
from .kmco_usage import SERVICE_RE, record
from .memory import current_base_commit
from .tools import ToolError

TARGET = 'kmco-core-memory'
OPERATION = 'same_task_observations'
PERMISSION = 'db.postgres.select'
SQL = ('SELECT operation,operation_ids,source_sha,result_state,result_sha256,attempt_count '
       'FROM circuit.core_memory WHERE mission_id=%(mission_id)s '
       'AND task_id=%(task_id)s AND source_sha=%(source_sha)s '
       'ORDER BY updated_at DESC LIMIT %(limit)s')


class NativeOwnerReadAuthority:
    """Non-JSON marker attached only by owner_cli after kernel/private-file checks."""
    def __init__(self):
        self.uid = os.getuid()


def scoped_database_context(ctx):
    if ctx.get('database_bindings'):
        return ctx
    authority = ctx.get('_native_owner_read_authority')
    if not isinstance(authority, NativeOwnerReadAuthority) or authority.uid != os.getuid():
        raise ToolError('D01_NATIVE_OR_DATABASE_AUTHORITY_REQUIRED')
    return bind_owner_readonly(ctx)


class NativeReader:
    def __init__(self, service):
        self.service = service

    def read(self, operation, contract, params, scope, limit):
        if operation != OPERATION or contract.get('sql') != SQL:
            raise ToolError('NATIVE_DATABASE_OPERATION_DENIED')
        try:
            import psycopg
            with psycopg.connect('service=' + self.service, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT current_database()')
                    if cur.fetchone() != ('km_microops',):
                        raise ToolError('NATIVE_DATABASE_TARGET_MISMATCH')
                conn.commit()
                return SQLReadAdapter(conn).read(operation, contract, params, scope, limit)
        except ToolError:
            raise
        except Exception:
            raise ToolError('NATIVE_DATABASE_READ_UNAVAILABLE') from None


def bind_owner_readonly(ctx):
    authority = ctx.get('_native_owner_read_authority')
    uid = os.getuid()
    if not isinstance(authority, NativeOwnerReadAuthority) or authority.uid != uid:
        raise ToolError('NATIVE_DATABASE_TRUSTED_OWNER_CONTEXT_REQUIRED')
    if uid == 0 or ctx.get('account_id') != 'posix:' + str(uid):
        raise ToolError('NATIVE_DATABASE_POSIX_OWNER_REQUIRED')
    service = ctx.get('kmco_pg_service')
    if not isinstance(service, str) or not SERVICE_RE.fullmatch(service):
        raise ToolError('NATIVE_DATABASE_EXISTING_SERVICE_REQUIRED')
    if ctx.get('database_bindings') or ctx.get('database_auth'):
        raise ToolError('NATIVE_DATABASE_EXISTING_AUTHORITY_PRESERVED')
    try:
        import psycopg
        with psycopg.connect('service=' + service, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute('SELECT current_database()')
                if cur.fetchone() != ('km_microops',):
                    raise ToolError('NATIVE_DATABASE_TARGET_MISMATCH')
                cur.execute("SELECT column_name,data_type FROM information_schema.columns "
                            "WHERE table_schema='circuit' AND table_name='core_memory' ORDER BY ordinal_position")
                columns = cur.fetchall()
                required = {'mission_id','task_id','source_sha','operation','operation_ids',
                            'result_state','result_sha256','attempt_count','updated_at'}
                if not required.issubset({c[0] for c in columns}):
                    raise ToolError('NATIVE_DATABASE_SCHEMA_UNAVAILABLE')
    except ToolError:
        raise
    except Exception:
        raise ToolError('NATIVE_DATABASE_BINDING_UNAVAILABLE') from None
    source = current_base_commit(ctx['workspace'])
    tenant = ctx['account_id']
    principal = tenant
    issuer = 'local-posix-owner:' + str(uid)
    now = time.time()
    scope = {k: ctx[k] for k in ('mission_id', 'task_id')}
    scoped = {**ctx, 'source_sha': source}
    def audit(event):
        # Original transaction commits its usage row and four-part projection.
        # Return only the committed row reference; never values or credentials.
        return record('kmco.d02.database_access', event, scoped,
                      ok=event.get('phase') != 'failed_or_ambiguous', return_ref=True)
    grant = {'role': 'READER', 'permissions': [PERMISSION],
             'mission_ids': [scope['mission_id']], 'task_ids': [scope['task_id']],
             'source_shas': [source], 'operations': [OPERATION]}
    binding = {'qualified': True, 'system': 'postgres', 'target_id': TARGET,
               'authority_ref': 'postgres:km_microops:circuit.core_memory',
               'tenant_id': tenant, 'schema_revision': hashlib.sha256(
                   json.dumps(columns, separators=(',', ':')).encode()).hexdigest(),
               'freshness_ttl_s': 60, 'issuer': issuer, 'audience': 'local-kmco-read',
               'principals': {principal: grant}, 'adapter': NativeReader(service), 'audit': audit,
               'operations': {OPERATION: {'action': 'select', 'parameters': {},
                   'sql': SQL, 'max_rows': 20, 'provenance_ref': 'postgres:km_microops:circuit.core_memory'}},
               'write_authorized': False}
    return {**ctx, 'source_sha': source, 'tenant_id': tenant,
            'database_bindings': {TARGET: binding},
            'database_auth': {'authenticated': True, 'principal_id': principal, 'role': 'READER',
                'issuer': issuer, 'audience': binding['audience'], 'permissions': [PERMISSION],
                **scope, 'source_sha': source, 'issued_at': now, 'expires_at': now + 60}}
