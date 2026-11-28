"""P09-P12 deterministic source bindings; no dispatch, inference or mutation."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .memory import current_base_commit
from .tools import ToolError, ToolSpec


def _scope(ctx):
    for key in ('mission_id', 'task_id', 'account_id', 'workspace'):
        if not isinstance(ctx.get(key), str) or not ctx[key]:
            raise ToolError('KMCO_RESILIENCE_SCOPE_REQUIRED')
    sha = current_base_commit(ctx['workspace'])
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ToolError('KMCO_RESILIENCE_SOURCE_REQUIRED')
    return sha


def _bounded_json(path, code):
    try:
        stat = path.lstat()
        if path.is_symlink() or not path.is_file() or stat.st_size > 65536:
            raise ToolError(code)
        value = json.loads(path.read_text(encoding='utf-8'))
    except ToolError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise ToolError(code) from None
    if not isinstance(value, dict):
        raise ToolError(code)
    return value


def run_p09(args, ctx):
    """Observe one existing QueueStore lease; never claim or renew it."""
    sha = _scope(ctx); store = ctx.get('store'); task_id = args['task_id']
    if store is None or not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', task_id):
        raise ToolError('P09_EXISTING_QUEUE_AND_TASK_REQUIRED')
    task = _bounded_json(store.doing / (task_id + '.json'), 'P09_TASK_READBACK_INVALID')
    lease = _bounded_json(store.doing / (task_id + '.lease.json'), 'P09_LEASE_READBACK_INVALID')
    hints = task.get('input', {}).get('hints', {})
    if (task.get('task_id') != task_id or task.get('mission_id') != ctx['mission_id']
            or hints.get('account_id') != ctx['account_id']
            or hints.get('base_commit') != sha or lease.get('task_id') != task_id):
        raise ToolError('P09_ISSUER_OR_SOURCE_SCOPE_MISMATCH')
    acquired, heartbeat, expires = (lease.get(k) for k in ('acquired_at','heartbeat_at','expires_at'))
    if any(type(v) not in (int, float) for v in (acquired, heartbeat, expires)):
        raise ToolError('P09_LEASE_TIMESTAMPS_INVALID')
    now = time.time(); active = expires > now and heartbeat >= acquired and heartbeat <= expires
    return {'circuit':'P09','steps_completed':[f'P09-M{i:02d}' for i in range(1,6)],
            'status':'ACTIVE_LEASE_OBSERVED_UNVERIFIED' if active else 'STALE_LEASE_DENIED',
            'task_id':task_id,'owner_node':task.get('owner_node'),
            'lease_fingerprint':hashlib.sha256(str(lease.get('lease_token','')).encode()).hexdigest(),
            'heartbeat_age_s':max(0, int(now-heartbeat)), 'source_sha':sha,
            'claim_performed':False,'heartbeat_performed':False,'commit_authorized':False,
            'independent_qc':'NOT_RUN'}


def run_p10(args, ctx):
    """Make a source-only deterministic-first recommendation; invoke nothing."""
    sha = _scope(ctx); request = args['request']
    if (not isinstance(request, dict) or set(request) != {'intent','deterministic_available','context'}
            or not isinstance(request['intent'], str) or not 1 <= len(request['intent']) <= 200
            or type(request['deterministic_available']) is not bool
            or not isinstance(request['context'], dict)):
        raise ToolError('P10_REQUEST_INVALID')
    raw = json.dumps(request['context'], sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > 32768:
        raise ToolError('P10_CONTEXT_BUDGET_EXCEEDED')
    route = 'DETERMINISTIC_EXISTING_HANDLER' if request['deterministic_available'] else 'MODEL_REQUIRES_SEPARATE_AUTHORITY'
    return {'circuit':'P10','steps_completed':[f'P10-M{i:02d}' for i in range(1,6)],
            'status':'ROUTE_RECOMMENDATION_NOT_EXECUTED','route':route,
            'context_sha256':hashlib.sha256(raw.encode()).hexdigest(),'context_bytes':len(raw.encode()),
            'source_sha':sha,'tokens_in':None,'tokens_out':None,'billed_cost_usd':None,
            'provider_invoked':False,'authority_granted':False,'independent_qc':'NOT_RUN'}


def run_p11(registry, args, ctx):
    """Reuse the original P06 capability/ACL preflight and emit no arguments."""
    _scope(ctx)
    from .kmco_p06 import run_p06
    result = run_p06(registry, args['tool'], args['arguments'], ctx)
    raw = json.dumps(args['arguments'], sort_keys=True, ensure_ascii=False, allow_nan=False)
    return {'circuit':'P11','steps_completed':[f'P11-M{i:02d}' for i in range(1,6)],
            'status':'SECURITY_PREFLIGHT_PASS_NOT_EXECUTED','tool':result['tool'],
            'arguments_sha256':hashlib.sha256(raw.encode()).hexdigest(),
            'arguments_returned':False,'secret_values_returned':False,
            'mutation_performed':False,'authority_granted':False,'independent_qc':'NOT_RUN'}


def run_p12(args, ctx):
    """Compare trusted, supplied capability envelopes; never probe or infer."""
    sha = _scope(ctx); providers = args['providers']; required = args['required']
    if (not isinstance(providers, list) or not 1 <= len(providers) <= 20
            or not isinstance(required, list) or not required
            or any(not isinstance(x, str) or not x for x in required)):
        raise ToolError('P12_CAPABILITY_INPUT_INVALID')
    rows=[]; seen=set()
    for row in providers:
        if (not isinstance(row, dict) or set(row) != {'provider','local','available','capabilities','evidence_ref'}
                or not isinstance(row['provider'], str) or not re.fullmatch(r'[a-z0-9-]{1,64}',row['provider'])
                or row['provider'] in seen or type(row['local']) is not bool
                or type(row['available']) is not bool or not isinstance(row['capabilities'],list)
                or any(not isinstance(x,str) or not x for x in row['capabilities'])
                or not isinstance(row['evidence_ref'],str) or not row['evidence_ref']):
            raise ToolError('P12_CAPABILITY_ENVELOPE_INVALID')
        seen.add(row['provider'])
        qualified = row['available'] and set(required).issubset(row['capabilities'])
        rows.append({'provider':row['provider'],'local':row['local'],'qualified':qualified,
                     'evidence_ref':row['evidence_ref'][:500]})
    eligible=[r for r in rows if r['qualified']]
    eligible.sort(key=lambda r:(not r['local'],r['provider']))
    return {'circuit':'P12','steps_completed':[f'P12-M{i:02d}' for i in range(1,6)],
            'status':'PARITY_EVIDENCE_REVIEWED_NOT_EXECUTED','required':required,
            'providers':rows,'preferred_provider':eligible[0]['provider'] if eligible else None,
            'source_sha':sha,'provider_invoked':False,'fallback_executed':False,
            'contract_parity_verified':len(eligible)>1,'authority_granted':False,
            'independent_qc':'NOT_RUN'}


def register_resilience(registry, catalog):
    entries=(
        ('P09','kmco.p09.lease',run_p09,{'task_id':'str'},{}),
        ('P10','kmco.p10.route',run_p10,{'request':'dict'},{}),
        ('P11','kmco.p11.security',lambda a,c:run_p11(registry,a,c),{'tool':'str','arguments':'dict'},{}),
        ('P12','kmco.p12.providers',run_p12,{'providers':'list','required':'list'},{}),
    )
    for group,name,fn,required,optional in entries:
        def call(args,ctx,group=group,fn=fn):
            if any(f'{group}-M{i:02d}' not in catalog._operations for i in range(1,6)):
                raise ToolError('KMCO_RESILIENCE_CATALOG_INCOMPLETE')
            return fn(args,ctx)
        registry.register(ToolSpec(name,'Deterministic source-bound resilience circuit',required,optional,
            'read','none','no dispatch, inference, mutation, grant or QC promotion'),call)
