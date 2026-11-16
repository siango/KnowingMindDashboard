"""Trusted middleware bindings for the existing registry and GUI tester.

No authentication is accepted from tool arguments. Exact argument bindings are
intentional: a grant to inspect one task must not become arbitrary file access.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .tools import ToolError, ToolSpec


class KMAdminContextProducer:
    """Existing Admin session -> CSRF-verified explicit read delegation.

    The server policy is required. A web role/account hint never becomes a
    KMCW grant here. Credentials stay in the host process, not config or logs.
    """
    ORIGIN = 'https://ai.knowingmind.app'

    def __init__(self, session_cookie):
        import re
        if not isinstance(session_cookie, str) or not re.fullmatch(
                r'__Host-km_web=[a-f0-9]{64}', session_cookie):
            raise ToolError('KM_ADMIN_EXISTING_SESSION_COOKIE_REQUIRED')
        self._cookie = session_cookie

    @classmethod
    def from_runtime_credentials(cls):
        """Consume an existing protected credential; never provision one."""
        import os
        import stat
        directory = os.environ.get('CREDENTIALS_DIRECTORY', '')
        if not directory:
            return None
        if (not Path(directory).is_absolute() or not hasattr(os, 'O_NOFOLLOW')
                or not hasattr(os, 'geteuid')):
            raise ToolError('KM_ADMIN_CREDENTIAL_CHANNEL_INVALID')
        path = Path(directory) / 'km-ai-admin.session-cookie'
        fd = None
        try:
            try:
                candidate = path.lstat()
            except FileNotFoundError:
                return None  # Other named credentials do not activate this path.
            if not stat.S_ISREG(candidate.st_mode):
                raise ToolError('KM_ADMIN_CREDENTIAL_PERMISSIONS_DENIED')
            parent = os.stat(directory, follow_symlinks=False)
            if (not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077
                    or parent.st_uid not in {0, os.geteuid()}):
                raise ToolError('KM_ADMIN_CREDENTIAL_PERMISSIONS_DENIED')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid not in {0, os.geteuid()}):
                raise ToolError('KM_ADMIN_CREDENTIAL_PERMISSIONS_DENIED')
            raw = os.read(fd, 1025)
            if len(raw) > 1024:
                raise ToolError('KM_ADMIN_SESSION_CREDENTIAL_INVALID')
            cookie = raw.decode('ascii').strip()
        except (OSError, UnicodeError):
            raise ToolError('KM_ADMIN_SESSION_CREDENTIAL_UNAVAILABLE') from None
        finally:
            if fd is not None:
                os.close(fd)
        return cls(cookie)

    @classmethod
    def runtime_context(cls, base):
        """Resolve the existing protected session afresh for each tool call.

        A credential delivered or rotated after connector startup must be
        consumed without retaining an absent or obsolete session. Discovery
        does not require Admin availability; execution still fails closed.
        """
        producer = cls.from_runtime_credentials()
        if producer is None:
            raise ToolError('KM_ADMIN_SESSION_CREDENTIAL_UNAVAILABLE')
        return producer(base)

    def _read(self, path, *, csrf=None):
        from urllib import request, error
        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        headers = {'Cookie': self._cookie, 'Accept': 'application/json',
                   'Cache-Control': 'no-store'}
        if csrf is not None:
            headers.update({'Origin': self.ORIGIN, 'X-Km-Csrf': csrf})
        req = request.Request(self.ORIGIN + path,
            data=b'{}' if csrf is not None else None,
            method='POST' if csrf is not None else 'GET', headers=headers)
        try:
            with request.build_opener(NoRedirect).open(req, timeout=10) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ToolError('KM_ADMIN_CONTEXT_RESPONSE_TOO_LARGE')
            value = json.loads(raw)
        except error.HTTPError as exc:
            code = ''
            try:
                remote = json.loads(exc.read(2048)).get('error')
                if remote in {'KMCW_DELEGATION_POLICY_NOT_CONFIGURED',
                              'KMCW_DELEGATION_DISABLED',
                              'KMCW_QUEUE_RESOURCE_REQUIRED',
                              'KMCW_DELEGATION_NOT_GRANTED',
                              'KMCW_READ_DELEGATION_REQUIRED',
                              'KMCW_DELEGATION_POLICY_INVALID',
                              'KMCW_ADMIN_REQUIRED','CSRF_REJECTED',
                              'ORIGIN_REJECTED','LOGIN_REQUIRED','SESSION_EXPIRED'}:
                    code = ':' + remote
            except (ValueError, AttributeError, TypeError):
                pass
            raise ToolError('KM_ADMIN_CONTEXT_HTTP_DENIED', str(exc.code)+code) from None
        except (error.URLError, TimeoutError, ValueError):
            raise ToolError('KM_ADMIN_CONTEXT_UNAVAILABLE') from None
        if not isinstance(value, dict):
            raise ToolError('KM_ADMIN_CONTEXT_CONTRACT_INVALID')
        return value

    def __call__(self, base):
        from .ingress import qualified, CATALOG, digest, idempotency_key
        from .tools import build_default_registry
        session = self._read('/api/session')
        csrf = session.get('csrf')
        if (session.get('authenticated') is not True
                or not isinstance(session.get('role'), str)
                or session.get('role') not in {'OWNER', 'ADMIN'}
                or not isinstance(csrf, str) or not csrf or len(csrf) > 1024
                or any(c in csrf for c in '\r\n')):
            raise ToolError('KM_ADMIN_OWNER_ADMIN_SESSION_REQUIRED')
        delegation = self._read('/api/kmcw/context', csrf=csrf)
        if (delegation.get('schema') != 'KMCW_VERIFIED_READ_CONTEXT_V1'
                or delegation.get('execution_authority') is not False):
            raise ToolError('KM_ADMIN_DELEGATION_CONTRACT_REQUIRED')
        fields = {'account_id','mission_id','task_id','command_auth',
                  'command_channels','tool_bindings'}
        if not fields.issubset(delegation):
            raise ToolError('KM_ADMIN_DELEGATION_SCOPE_MISSING')
        if (any(not isinstance(delegation[k], dict) for k in
                ('command_auth','command_channels','tool_bindings'))
                or any(not isinstance(v, dict) for v in delegation['tool_bindings'].values())):
            raise ToolError('KM_ADMIN_DELEGATION_CONTRACT_REQUIRED')
        supplied = {k: delegation[k] for k in fields}
        supplied['read_only'] = True
        candidate = bind_runtime_context(base, lambda scope: supplied)
        auth = candidate['command_auth']
        if (not isinstance(auth.get('permissions'), list) or not auth['permissions']
                or auth.get('channel') != 'ADMIN_V2' or auth.get('method') != 'SESSION_CSRF'
                or not qualified(candidate) or auth.get('issuer') != self.ORIGIN
                or auth.get('role') != session['role']):
            raise ToolError('KM_ADMIN_DELEGATION_IDENTITY_DENIED')
        specs = {s['name']:s for s in build_default_registry().command_contract()['commands']}
        channels = [n for n in auth['permissions'] if n in CATALOG]
        if len(channels) > 1:
            raise ToolError('KM_ADMIN_ONE_CHANNEL_READ_PER_CONTEXT')
        for name in auth['permissions']:
            spec = specs.get(name)
            if (spec is None or spec['permission'] != 'read'
                    or name.startswith('db.')):
                raise ToolError('KM_ADMIN_READ_DELEGATION_UNSUPPORTED', name)
            if name == 'queue_health' and not isinstance(
                    candidate['tool_bindings'].get(name, {}).get('resource_ref'), str):
                raise ToolError('KM_ADMIN_QUEUE_RESOURCE_REQUIRED')
            if name in CATALOG:
                import uuid
                binding = candidate['tool_bindings'].get(name, {})
                template = binding.get('arguments', {}).get('command', {})
                scope = template.get('scope') if isinstance(template, dict) else None
                if (not isinstance(scope, dict)
                        or set(scope) != {'plan_id','allowed_paths','payload'}):
                    raise ToolError('KM_ADMIN_CHANNEL_SCOPE_REQUIRED')
                envelope = {'command_id':uuid.uuid4().hex,
                    'correlation_id':uuid.uuid4().hex,
                    'principal_id':auth['principal_id'],'channel':'ADMIN_V2',
                    'mission_id':candidate['mission_id'],'task_id':candidate['task_id'],
                    'intent':CATALOG[name][0],'scope':scope,'approval_ref':'',
                    'source_hash':binding.get('source_sha'),'timestamp':auth['issued_at']}
                envelope['idempotency_key'] = idempotency_key(envelope)
                auth['body_sha256'] = digest(envelope)
                binding['arguments'] = {'command':envelope}
            if not authorized(spec, candidate):
                raise ToolError('KM_ADMIN_DELEGATION_SCOPE_DENIED', name)
        supplied['km_admin_binding'] = {'qualified': True, 'origin': self.ORIGIN,
                                        'session_cookie': self._cookie}
        return supplied


def bind_runtime_context(ctx, producer):
    """Accept an existing in-process verified producer, never config claims.

    This is a context handoff, not an authenticator or grant issuer. No CLI,
    environment import path, JSON file or tool argument selects the producer.
    Existing per-call checks remain authoritative after this handoff.
    """
    if producer is None:
        return ctx
    if not callable(producer):
        raise ToolError('MCP_CONTEXT_PRODUCER_REQUIRED')
    from types import MappingProxyType
    supplied = producer(MappingProxyType(dict(ctx)))
    allowed = {'command_auth', 'command_channels', 'tool_bindings',
               'database_auth', 'database_bindings', 'command_approvals',
               'km_admin_binding', 'gui_binding', 'tool_registry',
               'account_id', 'mission_id', 'task_id', 'source_sha', 'read_only',
               'memory_context'}
    if not isinstance(supplied, dict) or set(supplied) - allowed:
        raise ToolError('MCP_CONTEXT_FIELDS_DENIED')
    for field in ('command_auth','command_channels','tool_bindings','database_auth',
                  'database_bindings','command_approvals','km_admin_binding','gui_binding'):
        if field in supplied and not isinstance(supplied[field], dict):
            raise ToolError('MCP_CONTEXT_MAPPING_INVALID', field)
    if 'read_only' in supplied and type(supplied['read_only']) is not bool:
        raise ToolError('MCP_CONTEXT_READONLY_INVALID')
    if 'memory_context' in supplied:
        # Already-read V4 data is advisory context, never identity or a grant.
        memory = supplied['memory_context']
        if (not isinstance(memory, dict)
                or memory.get('schema') not in {
                    'DHAMMA_MEMORY_CONTEXT_V1', 'DHAMMA_MEMORY_SEARCH_V1'}
                or not isinstance(memory.get('items'), list)
                or not isinstance(memory.get('decisions'), list)
                or len(memory['items']) > 20 or len(memory['decisions']) > 20
                or any(not isinstance(row, dict) for row in
                       memory['items'] + memory['decisions'])):
            raise ToolError('MCP_MEMORY_CONTEXT_INVALID')
        try:
            raw = json.dumps(memory, ensure_ascii=False)
        except (ValueError, TypeError):
            raise ToolError('MCP_MEMORY_CONTEXT_INVALID') from None
        if len(raw.encode('utf-8')) > 200_000:
            raise ToolError('MCP_MEMORY_CONTEXT_TOO_LARGE')
    if (ctx.get('account_id') is not None and 'account_id' in supplied
            and supplied['account_id'] != ctx['account_id']):
        raise ToolError('MCP_CONTEXT_ACCOUNT_MISMATCH')
    if ctx.get('read_only') and supplied.get('read_only') is False:
        raise ToolError('MCP_CONTEXT_READONLY_DOWNGRADE_DENIED')
    # Preserve store/workspace/source configuration; no new queue or target.
    return {**ctx, **supplied}


def authorized(spec, ctx, args=None):
    from .ingress import qualified
    from .memory import current_base_commit
    if not qualified(ctx):
        return False
    auth = ctx['command_auth']
    role = ctx['command_channels'][auth['channel']]['principals'][auth['principal_id']]
    bindings = ctx.get('tool_bindings', {})
    if not isinstance(bindings, dict):
        return False
    binding = bindings.get(spec['name'], {})
    if not isinstance(binding, dict):
        return False
    if spec['name'] == 'queue_health' and 'resource_ref' in binding:
        store = ctx.get('store')
        if (store is None or not hasattr(store, 'base')
                or binding['resource_ref'] != 'queue-store:' + str(Path(store.base).resolve())):
            return False
    if (binding.get('qualified') is not True
            or spec['name'] not in auth['permissions']
            or auth.get('account_id') != ctx.get('account_id')
            or role.get('account_id') != ctx.get('account_id')
            or not ctx.get('task_id') or not ctx.get('mission_id')
            or auth.get('mission_id') != ctx['mission_id']
            or ctx['mission_id'] not in role.get('mission_ids', [])
            or ctx['task_id'] not in role.get('task_ids', [])
            or ctx['task_id'] not in auth.get('task_ids', [])
            or any(binding.get(k) != ctx.get(k) for k in ('account_id', 'mission_id', 'task_id'))
            or not binding.get('source_sha')
            or binding.get('source_sha') != current_base_commit(ctx.get('workspace', ''))
            or not isinstance(binding.get('arguments'), dict)
            or not set(spec['required_permissions']).issubset(auth['permissions'])):
        return False
    # A catalog entry never approves a mutation. Reuse the existing verified
    # approval/readback contract, then retain registry and handler policy gates.
    if spec['permission'] != 'read' and spec['name'] != 'gui_test':
        from .ingress import _approval
        if ctx.get('read_only') or binding.get('write_authorized') is not True:
            return False
        try:
            _approval({'approval_ref': binding.get('approval_ref'),
                'principal_id': auth['principal_id'], 'mission_id': ctx['mission_id'],
                'task_id': ctx['task_id'], 'source_hash': binding['source_sha'],
                'scope': binding.get('arguments')}, ctx, spec['name'].upper())
        except ToolError:
            return False
    if spec['name'] == 'gui_test' and (ctx.get('read_only') or binding.get('write_authorized') is not True):
        return False
    if spec['name'] == 'gui_test':
        from .ingress import _approval
        gui = ctx.get('gui_binding', {})
        try:
            _approval({'approval_ref': gui.get('approval_ref'),
                'principal_id': auth['principal_id'], 'mission_id': ctx['mission_id'],
                'task_id': ctx['task_id'], 'source_hash': binding['source_sha'],
                'scope': gui.get('scope')}, ctx, 'GUI_TEST')
        except ToolError:
            return False
    if args is None:
        return True
    try:
        return json.dumps(args, sort_keys=True, allow_nan=False) == json.dumps(
            binding['arguments'], sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return False


def ingest_gui(ctx, task_id):
    """Verify existing tester artifacts and append once to the existing task."""
    binding = ctx.get('gui_binding', {})
    if (binding.get('qualified') is not True or binding.get('task_id') != task_id
            or binding.get('mission_id') != ctx.get('mission_id')
            or binding.get('account_id') != ctx.get('account_id')):
        raise ToolError('GUI_BINDING_UNQUALIFIED')
    if any(not isinstance(binding.get(k), str) or not binding[k]
           for k in ('evidence_path', 'screenshot_path')):
        raise ToolError('GUI_ARTIFACT_BINDING_INVALID')
    path = Path(binding['evidence_path'])
    raw = path.read_bytes()
    if len(raw) > 1_000_000:
        raise ToolError('GUI_EVIDENCE_TOO_LARGE')
    data = json.loads(raw)
    if (not isinstance(data, dict)
            or data.get('schema') != 'KMCW_GUI_EVIDENCE_V1'
            or data.get('task_id') != task_id
            or data.get('mission_id') != ctx['mission_id']):
        raise ToolError('GUI_EVIDENCE_SCOPE_DENIED')
    if not isinstance(data.get('screenshot'), str) or not data['screenshot']:
        raise ToolError('GUI_EVIDENCE_SCOPE_DENIED')
    screenshot = Path(binding['screenshot_path'])
    if (Path(data.get('screenshot', '')).resolve() != screenshot.resolve()
            or hashlib.sha256(screenshot.read_bytes()).hexdigest() != data.get('screenshot_sha256')):
        raise ToolError('GUI_SCREENSHOT_HASH_MISMATCH')
    digest = hashlib.sha256(raw).hexdigest()
    event = ctx['store'].append_event(task_id, 'gui.evidence', 'OBSERVED',
        hashlib.sha256(f"{ctx['account_id']}:{task_id}:{digest}".encode()).hexdigest(),
        account_id=ctx['account_id'], mission_id=ctx['mission_id'],
        detail={'sha256': digest, 'status': data.get('status'),
                'evidence_refs': [str(path), str(screenshot)],
                'independent_verified': False, 'backend_verified': False},
        provenance='existing Playwright tester evidence; not admission or QC')
    return {'sha256': digest, 'delivery': event['delivery'], 'event_hash': event['event_hash'],
            'independent_verified': False, 'backend_verified': False}


def gui_health(ctx):
    binding = ctx.get('gui_binding', {})
    return (binding.get('qualified') is True and callable(binding.get('run')),
            'EXISTING_GUI_TESTER_BINDING_REQUIRED')


def gui_test(args, ctx):
    """Call the existing tester once. Ambiguous interruptions never auto-replay."""
    import fcntl
    spec = {'name': 'gui_test', 'permission': 'write', 'required_permissions': []}
    if not authorized(spec, ctx, args):
        raise ToolError('MCP_TOOL_SCOPE_DENIED')
    if not gui_health(ctx)[0]:
        raise ToolError('GUI_TESTER_UNAVAILABLE')
    binding = ctx['gui_binding']
    if not args['request_id'].strip() or len(args['request_id']) > 128 or not isinstance(binding.get('scope'), dict):
        raise ToolError('GUI_REQUEST_INVALID')
    if any(binding.get(k) != ctx.get(k) for k in ('account_id', 'mission_id', 'task_id')):
        raise ToolError('GUI_EVIDENCE_SCOPE_DENIED')
    from .ingress import digest
    scope_hash = digest(binding['scope'])
    source_sha = ctx['tool_bindings']['gui_test']['source_sha']
    def matching(data):
        return (isinstance(data, dict)
                and data.get('request_id') == args['request_id']
                and data.get('scope_sha256') == scope_hash
                and data.get('source_sha') == source_sha)
    key = '.gui-' + hashlib.sha256(json.dumps({
        'account': ctx['account_id'], 'mission': ctx['mission_id'],
        'task': ctx['task_id'], 'request': args, 'scope': binding.get('scope'),
        'source': ctx['tool_bindings']['gui_test']['source_sha']}, sort_keys=True).encode()).hexdigest()
    store = ctx['store']
    with (store.checkpoints / f'{key}.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        saved = store.load_checkpoint(key)
        if saved is None and (store.checkpoints / f'{key}.json').exists():
            raise ToolError('GUI_CHECKPOINT_UNREADABLE')
        if saved:
            result = saved['data'].get('result')
            if result is not None:
                return {**result, 'duplicate': True}
            # Evidence written before interruption can be ingested without
            # repeating browser actions. Missing evidence stays blocked.
            data = json.loads(Path(binding['evidence_path']).read_bytes())
            if not matching(data):
                raise ToolError('GUI_ACTION_OUTCOME_UNKNOWN')
        else:
            store.save_checkpoint(key, 'gui_intent', {'request_id': args['request_id']})
            binding['run'](request_id=args['request_id'], scope=dict(binding['scope']),
                           source_sha=source_sha)
        data = json.loads(Path(binding['evidence_path']).read_bytes())
        if not matching(data):
            raise ToolError('GUI_REQUEST_READBACK_MISMATCH')
        result = ingest_gui(ctx, ctx['task_id'])
        store.save_checkpoint(key, 'gui_observed', {'result': result})
        return result


def register_gui(registry):
    for spec in registry.command_contract()['commands']:
        words = set(spec['name'].lower().replace('.', '_').replace('-', '_').split('_'))
        if (words & {'gui', 'browser', 'playwright'}
                and words & {'test', 'run', 'execute', 'interact'}):
            return
    registry.register(ToolSpec('gui_test', 'existing scoped GUI tester',
        {'request_id': 'str'}, {}, 'write', 'existing browser actions and local evidence',
        'trusted callable and exact scope required; ambiguous actions never replay'),
        gui_test, gui_health)
