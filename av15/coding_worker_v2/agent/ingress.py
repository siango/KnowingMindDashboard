"""One logical command contract over existing handlers; no HTTP server or auth issuer.

Authentication/roles/approvals must come from independently authenticated existing
MCP OAuth, LINE signature+owner mapping, or Admin session+CSRF middleware.
"""
from __future__ import annotations
import hashlib
import json
import math
import time

CATALOG = {
    'kmcw.context': ('CONTEXT', 'read'),
    'kmcw.advise': ('ADVISE', 'write'),
    'kmcw.architect': ('ARCHITECT', 'write'),
    'kmcw.mission.plan': ('PLAN', 'write'),
    'kmcw.mission.submit': ('SUBMIT', 'write'),
    'kmcw.mission.status': ('STATUS', 'read'),
    'kmcw.task.inspect': ('INSPECT', 'read'),
    'kmcw.mission.control': ('CONTROL', 'write'),
    'kmcw.approval.record': ('APPROVE', 'write'),
    'kmcw.evidence.read': ('EVIDENCE', 'read'),
}
METHODS = {'CHATGPT': 'MCP_OAUTH', 'LINE': 'LINE_SIGNATURE_OWNER_MAP', 'ADMIN_V2': 'SESSION_CSRF'}
FIELDS = {'command_id','idempotency_key','principal_id','channel','mission_id','task_id',
          'intent','scope','approval_ref','source_hash','timestamp','correlation_id'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def idempotency_key(envelope):
    # Channel/transport IDs are delivery identity, never a second execution key.
    semantic = {k: envelope[k] for k in ('principal_id','mission_id','task_id',
                'intent','scope','approval_ref','source_hash')}
    if envelope['intent'] == 'CONTROL':
        # A new control transition is a new logical operation. Retries across
        # channels must preserve this canonical ID, not a transport delivery ID.
        semantic['command_id'] = envelope['command_id']
    return digest(semantic)


def _now(ctx):
    return ctx.get('clock', time.time)()


def qualified(ctx):
    auth = ctx.get('command_auth', {})
    if not isinstance(auth, dict):
        return False
    channel = auth.get('channel')
    subject = auth.get('principal_id')
    channels = ctx.get('command_channels', {})
    if (not isinstance(channel, str) or channel not in METHODS
            or not isinstance(subject, str) or not subject
            or not isinstance(auth.get('role'), str)
            or not isinstance(channels, dict)):
        return False
    config = channels.get(channel, {})
    if not isinstance(config, dict) or not isinstance(config.get('principals', {}), dict):
        return False
    role = config.get('principals', {}).get(subject, {})
    if not isinstance(role, dict):
        return False
    for permissions in (auth.get('permissions'), role.get('permissions', [])):
        if (not isinstance(permissions, list)
                or any(not isinstance(p, str) or not p for p in permissions)):
            return False
    for scope in (auth.get('task_ids', []), role.get('mission_ids', []),
                  role.get('task_ids', [])):
        if not isinstance(scope, list) or any(not isinstance(v, str) for v in scope):
            return False
    now = _now(ctx)
    if type(now) not in (int, float) or not math.isfinite(now):
        return False
    return (channel in METHODS and config.get('qualified') is True
            and config.get('method') == METHODS[channel] == auth.get('method')
            and config.get('issuer') == auth.get('issuer') and bool(config.get('issuer'))
            and auth.get('authenticated') is True and auth.get('role') in {'OWNER','ADMIN','OBSERVER'}
            and role.get('role') == auth.get('role')
            and isinstance(auth.get('permissions'), list)
            and set(auth['permissions']).issubset(role.get('permissions', []))
            and isinstance(auth.get('principal_id'), str) and bool(auth['principal_id'])
            and all(type(auth.get(k)) in (int,float) and math.isfinite(auth[k]) for k in ('issued_at','expires_at'))
            and 0 <= now - auth['issued_at'] <= 60 and now < auth['expires_at'] <= auth['issued_at'] + 60)


def _error(reason):
    from .tools import ToolError
    raise ToolError(reason)


def _approval(envelope, ctx, action):
    approval = ctx.get('command_approvals', {}).get(envelope['approval_ref'], {})
    now = _now(ctx)
    if (approval.get('verified') is not True or approval.get('readback_confirmed') is not True
            or approval.get('principal_id') != envelope['principal_id']
            or approval.get('mission_id') != envelope['mission_id']
            or approval.get('task_id') != envelope['task_id']
            or approval.get('action') != action or approval.get('source_hash') != envelope['source_hash']
            or approval.get('scope_sha256') != digest(envelope['scope'])
            or not approval.get('evidence_ref')
            or any(type(approval.get(k)) not in (int,float) or not math.isfinite(approval[k])
                   for k in ('issued_at','expires_at'))
            or not approval['issued_at'] <= now < approval['expires_at'] <= approval['issued_at'] + 3600):
        _error('COMMAND_SCOPED_APPROVAL_UNAVAILABLE_OR_EXPIRED')
    return approval


def execute(name, args, ctx):
    from . import memory, pilot, tracking
    from .conversation import _digest
    import fcntl
    if name not in CATALOG or not qualified(ctx):
        _error('COMMAND_CHANNEL_NOT_QUALIFIED')
    envelope = args['command']
    if not isinstance(envelope, dict) or set(envelope) != FIELDS:
        _error('COMMAND_ENVELOPE_INVALID')
    for key in ('command_id','correlation_id'):
        pilot._identifier(envelope[key])
    if any(not isinstance(envelope[k], str) for k in FIELDS - {'scope','timestamp'}):
        _error('COMMAND_ENVELOPE_INVALID')
    if not isinstance(envelope['scope'], dict) or set(envelope['scope']) != {'plan_id','allowed_paths','payload'}:
        _error('COMMAND_SCOPE_INVALID')
    payload = envelope['scope']['payload']
    if not isinstance(payload, dict) or len(json.dumps(envelope).encode()) > 64_000:
        _error('COMMAND_ENVELOPE_INVALID')
    auth = ctx['command_auth']
    principal = ctx['command_channels'][auth['channel']]['principals'][auth['principal_id']]
    if (auth.get('body_sha256') != digest(envelope) or auth['channel'] != envelope['channel']
            or auth['principal_id'] != envelope['principal_id']
            or auth.get('mission_id') != envelope['mission_id']
            or auth.get('account_id') != ctx['account_id'] or principal.get('account_id') != ctx['account_id']
            or envelope['mission_id'] not in principal.get('mission_ids', [])
            or envelope['task_id'] not in principal.get('task_ids', [])
            or envelope['mission_id'] != ctx['mission_id']
            or envelope['task_id'] not in auth.get('task_ids', [])
            or name not in auth.get('permissions', []) or envelope['intent'] != CATALOG[name][0]):
        _error('COMMAND_PRINCIPAL_OR_ACL_DENIED')
    now = _now(ctx)
    if type(envelope['timestamp']) not in (int,float) or not math.isfinite(envelope['timestamp']) or not 0 <= now - envelope['timestamp'] <= 60:
        _error('COMMAND_STALE')
    plan, _ = pilot._plan(envelope['scope']['plan_id'], ctx)
    identities = [f'{plan.plan_id}-{t.task_id}' for t in plan.tasks]
    if (plan.mission_id != envelope['mission_id'] or envelope['task_id'] not in identities
            or envelope['source_hash'] != memory.current_base_commit(ctx['workspace'])
            or envelope['scope']['allowed_paths'] != ctx['pilot']['allowed_paths']):
        _error('COMMAND_TASK_SOURCE_OR_SCOPE_MISMATCH')
    if envelope['idempotency_key'] != idempotency_key(envelope):
        _error('COMMAND_IDEMPOTENCY_INVALID')
    task = next(t for t in plan.tasks if f'{plan.plan_id}-{t.task_id}' == envelope['task_id'])
    action = payload.get('action') if name in {'kmcw.mission.control','kmcw.approval.record'} else envelope['intent']
    mutating = action in {'SUBMIT','EXECUTE','PAUSE','RESUME','APPROVE'}
    if name == 'kmcw.mission.control' and action not in {'EXECUTE','PAUSE','RESUME'}:
        _error('COMMAND_CONTROL_ACTION_DENIED')
    if name == 'kmcw.approval.record' and action not in {'SUBMIT','EXECUTE','PAUSE','RESUME'}:
        _error('COMMAND_APPROVAL_ACTION_DENIED')
    execution_ids = [envelope['task_id']]
    if name == 'kmcw.mission.control' and action in {'PAUSE','RESUME'}:
        declared = payload.get('task_ids')
        if (payload.get('control_scope') != 'PLAN' or not isinstance(declared, list)
                or any(not isinstance(t, str) for t in declared)
                or len(declared) != len(set(declared)) or set(declared) != set(identities)
                or not set(declared).issubset(auth.get('task_ids', []))
                or not set(declared).issubset(principal.get('task_ids', []))):
            _error('COMMAND_PLAN_CONTROL_SCOPE_REQUIRED')
        execution_ids = declared
    parent_scope = ctx.get('execution_task_ids')
    if parent_scope is not None and (not isinstance(parent_scope, (list,tuple))
            or not set(execution_ids).issubset(parent_scope)):
        _error('COMMAND_PARENT_TASK_SCOPE_DENIED')
    if mutating:
        if auth['role'] not in {'OWNER','ADMIN'} or (name == 'kmcw.mission.control'
                and f'{name}:{action}' not in auth.get('permissions', [])):
            _error('COMMAND_MUTATION_ACL_DENIED')
        approval = _approval(envelope, ctx, action)
    if name == 'kmcw.context':
        return pilot.context({}, ctx)
    if name in {'kmcw.mission.status', 'kmcw.evidence.read'}:
        return tracking.snapshot(plan, ctx, now=now)
    if name == 'kmcw.task.inspect':
        from .self_development import inspect
        return inspect(plan, task.task_id, ctx)
    if name in {'kmcw.advise','kmcw.architect','kmcw.mission.plan'}:
        # Force a non-executing intent; arbitrary payload text cannot select a handler.
        return pilot.discuss({'plan_id': plan.plan_id, 'session_id': envelope['correlation_id'],
            'request_id': envelope['command_id'], 'text': envelope['intent'] + ' ' + str(payload.get('text',''))[:3500]}, ctx)
    store = ctx['store']
    key = '.command-' + envelope['idempotency_key']
    runtime = ctx.get('tracking_runtime', {})
    availability_fingerprint = digest(runtime)
    live = (tracking.fresh(runtime, now=now) and runtime.get('online') is True
            and runtime.get('owner_node') == plan.owner_node
            and runtime.get('mission_id') == plan.mission_id and runtime.get('source_sha') == envelope['source_hash'])
    with (store.checkpoints / f'{key}.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous = store.load_checkpoint(key)
        if previous is None and (store.checkpoints / f'{key}.json').exists():
            _error('COMMAND_CHECKPOINT_UNREADABLE')
        if previous:
            record = previous.get('data', {})
            if record.get('semantic_sha256') != envelope['idempotency_key']:
                _error('COMMAND_IDEMPOTENCY_CONFLICT')
            recover_nonexecution = (record['result'].get('status') == 'UNAVAILABLE'
                and record.get('execution_started') is False
                and record.get('resumable_unavailable') is True and live
                and record.get('availability_fingerprint') != availability_fingerprint)
            if not recover_nonexecution:
                return {**record['result'], 'delivery': 'DUPLICATE_ATTACHED'}
        execution_started = False
        result = {'mission_id': plan.mission_id, 'task_id': envelope['task_id'],
                  'idempotency_key': envelope['idempotency_key'], 'status': 'PENDING_READBACK',
                  'dispatch': False, 'central_sync': 'UNSYNCED', 'admit': 'ADMIT_BLOCKED'}
        def persist():
            with store._locked():
                store.save_checkpoint(key, 'command_ingress', {'semantic_sha256': envelope['idempotency_key'],
                    'command_id': envelope['command_id'], 'correlation_id': envelope['correlation_id'],
                    'principal_id': auth['principal_id'], 'channel': envelope['channel'], 'result': result,
                    'execution_started': execution_started, 'availability_fingerprint': availability_fingerprint,
                    'resumable_unavailable': result['status'] == 'UNAVAILABLE' and not execution_started})
        persist()  # restart preserves ambiguity instead of repeating side effects
        if name == 'kmcw.approval.record':
            result.update(status='SCOPED_APPROVAL_REFERENCE_RECORDED', approval_ref=envelope['approval_ref'],
                          approval_evidence_ref=approval['evidence_ref'], independent_QC='NOT_GRANTED')
        elif name == 'kmcw.mission.submit':
            result.update(status='SUBMITTED_REQUIRES_ADMIT' if live else 'UNAVAILABLE',
                          reason='EXISTING_PLAN_ATTACHED_NO_NEW_TASK_OR_QUEUE', created=False,
                          next_action='EXISTING_SUPERVISOR_RECONCILE_WHEN_AUTHORIZED')
        else:
            request = {'session_id': envelope['correlation_id'], 'request_id': envelope['command_id'],
                       'plan_id': plan.plan_id, 'text': action}
            forwarded = {**ctx, 'execution_task_ids': execution_ids,
                'owner_command': {'authenticated': True, 'subject': auth['principal_id'],
                'account_id': ctx['account_id'], 'mission_id': plan.mission_id, 'plan_id': plan.plan_id,
                'source_sha': envelope['source_hash'], 'request_sha256': _digest(request),
                'permissions': ['mission:' + action.lower()]}}
            if action == 'EXECUTE' and not live:
                result.update(status='UNAVAILABLE', reason='AV15_OFFLINE_OR_UNQUALIFIED')
            else:
                execution_started = True
                persist()  # any interruption after this point is ambiguous
                response = pilot.discuss(request, forwarded)
                result.update(status=response['execution'], response=response)
        store.append_event(envelope['task_id'], 'command.accepted', result['status'],
            digest([envelope['idempotency_key'], result['status'], availability_fingerprint]),
            mission_id=plan.mission_id, account_id=ctx['account_id'],
            command_id=envelope['command_id'], owner_node=plan.owner_node, source_sha=envelope['source_hash'],
            provenance='EXISTING_CHANNEL_AUTHENTICATION_CONTRACT',
            detail={'channel': envelope['channel'], 'principal_id': auth['principal_id'], 'intent': envelope['intent']})
        persist()
        return result


def register(registry):
    from .tools import ToolSpec
    for name, (_, permission) in CATALOG.items():
        registry.register(ToolSpec(name, 'unified existing KMCW command interface', {'command':'dict'}, {},
            permission, 'existing scoped checkpoints only; execution retains ADMIT/QC' if permission == 'write' else 'none',
            'qualified channel middleware; canonical command envelope; current task ACL'),
            lambda args, ctx, name=name: execute(name, args, ctx),
            lambda ctx: (True, 'QUALIFIED_CHANNEL_CONTRACT') if qualified(ctx) else (False, 'COMMAND_CHANNEL_NOT_QUALIFIED'))


def command_schema(name):
    """Exact discoverable JSON Schema for the common command envelope."""
    return {'type':'object','additionalProperties':False,'required':sorted(FIELDS),
        'properties':{
            **{key:{'type':'string','minLength':1,'maxLength':160} for key in
               ('command_id','principal_id','mission_id','task_id','correlation_id')},
            'idempotency_key':{'type':'string','pattern':'^[0-9a-f]{64}$'},
            'channel':{'type':'string','enum':list(METHODS)},
            'intent':{'type':'string','enum':[CATALOG[name][0]]},
            'scope':{'type':'object','additionalProperties':False,
                     'required':['plan_id','allowed_paths','payload'], 'properties':{
                'plan_id':{'type':'string','minLength':1,'maxLength':121},
                'allowed_paths':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':10,'uniqueItems':True},
                'payload':{'type':'object'}}},
            'approval_ref':{'type':'string','maxLength':160},
            'source_hash':{'type':'string','pattern':'^[0-9a-f]{40}$'},
            'timestamp':{'type':'number'}}}
