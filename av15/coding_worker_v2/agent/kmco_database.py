"""Native D01/D02 circuits over the original database authorization/adapters.

No SQL/credential/connection from tool arguments. No source-owner promotion.
Each stage retains the existing database identity, exact operation and audit.
"""
import json
from datetime import datetime, timezone

from .database_tools import authorize, health, OPS, WRITES
from .tools import ToolError

READ_ACTIONS = {'postgres': {'select'}, 'd1': {'select'},
                'pgvector': {'search'}, 'memory': {'read'}, 'r2': {'get', 'list', 'metadata'}}


def enumerate_authority_domains(ctx):
    bindings = ctx.get('database_bindings')
    if not isinstance(bindings, dict):
        raise ToolError('D01_DATABASE_BINDINGS_REQUIRED')
    return bindings


def resolve_dataset_owner(bindings, system, target):
    if system not in READ_ACTIONS or not isinstance(target, str):
        raise ToolError('D01_SYSTEM_OR_TARGET_INVALID')
    binding = bindings.get(target)
    if (not isinstance(binding, dict) or binding.get('system') != system
            or binding.get('target_id') != target or not binding.get('authority_ref')):
        raise ToolError('D01_EXACT_OWNER_BINDING_REQUIRED')
    return binding


def check_source_health(ctx, system, action, target):
    # Local qualification is explicit; remote health is established by the read.
    auth, binding, grant = authorize(ctx, system, action, target)
    if not callable(getattr(binding.get('adapter'), 'read', None)):
        raise ToolError('D01_READ_ADAPTER_UNAVAILABLE')
    return auth, binding, grant


def bind_schema_revision(binding, operation):
    revision = binding.get('schema_revision')
    contract = binding.get('operations', {}).get(operation)
    if (not isinstance(revision, str) or not revision.strip()
            or not isinstance(contract, dict) or not isinstance(contract.get('parameters'), dict)):
        raise ToolError('D01_SCHEMA_REVISION_OR_OPERATION_REQUIRED')
    return revision, contract


def select_authorized_adapter(registry, ctx, system, target, operation):
    binding = resolve_dataset_owner(enumerate_authority_domains(ctx), system, target)
    revision, contract = bind_schema_revision(binding, operation)
    action = contract.get('action')
    if action not in READ_ACTIONS[system]:
        raise ToolError('D01_READ_ACTION_REQUIRED')
    auth, binding, grant = check_source_health(ctx, system, action, target)
    name = 'db.' + system + '.' + action
    if name not in registry.names() or operation not in grant.get('operations', []):
        raise ToolError('D01_OPERATION_NOT_AUTHORIZED')
    return name, revision, binding, auth


def run_d01(registry, args, ctx):
    name, revision, binding, auth = select_authorized_adapter(
        registry, ctx, args['system'], args['target_id'], args['operation'])
    # M06: exact scoped provenance; never emit adapter instances or principals.
    return {'circuit': 'D01', 'steps_completed': [f'D01-M{i:02d}' for i in range(1, 7)],
            'status': 'AUTHORIZED_SOURCE_RESOLVED_REMOTE_NOT_PROBED',
            'target_id': args['target_id'], 'system': args['system'],
            'handler': name, 'authority_ref': binding['authority_ref'],
            'schema_revision': revision,
            'mission_id': ctx['mission_id'], 'task_id': ctx['task_id'],
            'source_sha': ctx['source_sha'], 'authority_granted': False,
            'independent_qc': 'NOT_RUN'}


def validate_read_request(registry, name, args):
    registry.validate_arguments(name, args)
    raw = json.dumps(args, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > 65536:
        raise ToolError('D02_INPUT_LIMIT')


def bind_tenant_and_scopes(ctx, binding):
    # A dataset may be account scoped or tenant scoped, but never guessed.
    tenant = binding.get('tenant_id')
    if not isinstance(tenant, str) or not tenant or ctx.get('tenant_id') != tenant:
        raise ToolError('D02_TENANT_SCOPE_REQUIRED')
    return tenant


def parameterize_query(contract, params):
    kinds = {'str': str, 'int': int, 'float': float, 'list': list, 'dict': dict, 'bool': bool}
    if (not isinstance(params, dict) or set(params) != set(contract['parameters'])
            or any(type(params[k]) is not kinds.get(kind)
                   for k, kind in contract['parameters'].items())):
        raise ToolError('D02_PARAMETER_CONTRACT_INVALID')
    # Original adapter owns approved SQL, placeholders and source row isolation.
    return dict(params)


def apply_limit_and_cursor(contract, limit, cursor):
    if type(limit) is not int or not 1 <= limit <= min(100, contract.get('max_rows', 100)):
        raise ToolError('D02_ROW_LIMIT')
    # Cursors are allowed only as an explicitly typed operation parameter;
    # this wrapper never invents a source watermark or interpolates SQL.
    if cursor is not None:
        raise ToolError('D02_CURSOR_MUST_BE_SOURCE_OPERATION_PARAMETER')
    return limit


def run_d02(registry, args, ctx):
    name, revision, binding, auth = select_authorized_adapter(
        registry, ctx, args['system'], args['target_id'], args['operation'])
    contract = binding['operations'][args['operation']]
    params = parameterize_query(contract, args.get('params', {}))
    call = {'target_id': args['target_id'], 'operation': args['operation'],
            'params': params, 'limit': apply_limit_and_cursor(contract, args.get('limit', 20), args.get('cursor'))}
    validate_read_request(registry, name, call)
    tenant = bind_tenant_and_scopes(ctx, binding)
    ttl = binding.get('freshness_ttl_s')
    if type(ttl) not in (int, float) or not 0 < ttl <= 3600:
        raise ToolError('D02_FRESHNESS_CONTRACT_REQUIRED')
    # M05: original ToolRegistry health/schema/sandbox + DB ACL + durable audit.
    result = registry.call(name, call, ctx, mcp=True)
    try:
        observed = datetime.fromisoformat(result['observed_at'])
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        valid = (observed.tzinfo is not None and 0 <= age <= ttl
                 and all(result.get(k) == ctx.get(k) for k in ('mission_id', 'task_id', 'source_sha'))
                 and result.get('target_id') == args['target_id'] and result.get('evidence_ref'))
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ToolError('D02_READBACK_PROVENANCE_OR_FRESHNESS_INVALID')
    # M06: return bounded original rows and exact owner metadata, never PASS QC.
    return {'circuit': 'D02', 'steps_completed': [f'D02-M{i:02d}' for i in range(1, 7)],
            'status': 'READ_EXECUTED_UNVERIFIED', 'readback': result,
            'tenant_id': tenant, 'authority_ref': binding['authority_ref'],
            'schema_revision': revision,
            'freshness_basis': 'READ_OBSERVATION_TIME_NOT_DATASET_WATERMARK',
            'independent_qc': 'NOT_RUN',
            'authority_granted': False}


def register_database_circuits(registry, catalog):
    from .tools import ToolSpec
    for group, name, fn, optional in (
        ('D01', 'kmco.d01.authority', run_d01, {}),
        ('D02', 'kmco.d02.read', run_d02, {'params': 'dict', 'limit': 'int', 'cursor': 'str'})):
        def handler(args, ctx, group=group, fn=fn):
            if any(f'{group}-M{i:02d}' not in catalog._operations for i in range(1, 7)):
                raise ToolError('KMCO_DOMAIN_CATALOG_INCOMPLETE')
            from .native_database import scoped_database_context
            return fn(registry, args, scoped_database_context(ctx))
        registry.register(ToolSpec(name, 'Original scoped database authority/read circuit',
            {'system': 'str', 'target_id': 'str', 'operation': 'str'}, optional,
            'read', 'original durable DB read audit only',
            'exact owner/tenant/schema/ACL; original adapters; no arbitrary SQL or credentials'), handler)
