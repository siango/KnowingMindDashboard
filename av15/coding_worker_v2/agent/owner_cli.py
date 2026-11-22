"""Explicit POSIX-owner circuit approval for the local CLI, never HTTP/JSON auth.

The kernel UID and owner-only config are the credential channel. This does not
impersonate an Admin session, issue remote credentials, or grant Canon access.
"""
from __future__ import annotations
import copy
import json
import os
import time
from pathlib import Path
from . import circuits
from .native_cloudflare import private_file
from .tools import ToolError

CHANNEL = 'SSH_OWNER'
METHOD = 'POSIX_UID_OWNER'


def load_owner_runtime(path):
    doc = private_file(path)
    if os.getuid() == 0 or doc.get('account_id') != 'posix:' + str(os.getuid()):
        raise ToolError('SSH_NATIVE_OWNER_IDENTITY_REQUIRED')
    config_path = Path(path).expanduser().resolve(strict=True)
    base = Path(doc.get('runtime_base', '')).expanduser()
    if not base.is_absolute(): base = config_path.parent / base
    base = base.resolve(strict=True)
    if not (base / 'queue').is_dir() or base.stat().st_uid != os.getuid():
        raise ToolError('SSH_EXISTING_RUNTIME_QUEUE_REQUIRED')
    from .circuit_cli import load_runtime
    ctx, reg = load_runtime(path)
    if Path(ctx['workspace']).stat().st_uid != os.getuid():
        raise ToolError('SSH_WORKSPACE_OWNER_REQUIRED')
    return ctx, reg


def invoke(ctx, registry, name, arguments, *, child_tools=(), approved=False):
    if approved is not True or os.getuid() == 0:
        raise ToolError('SSH_EXPLICIT_APPROVAL_REQUIRED')
    if ctx.get('account_id') != 'posix:' + str(os.getuid()):
        raise ToolError('SSH_NATIVE_OWNER_IDENTITY_REQUIRED')
    allowed = {'kmcp.circuit.submit', 'kmcp.circuit.run'}
    if name not in allowed:
        raise ToolError('SSH_APPROVAL_OPERATION_DENIED')
    contracts = {x['name']: x for x in registry.command_contract()['commands']}
    for tool in child_tools:
        if (tool not in contracts or tool.startswith(('db.', 'kmcw.', 'kcc.', 'kmcp.'))
                or contracts[tool]['permission'] == 'destructive'):
            raise ToolError('SSH_CHILD_AUTHORITY_DENIED')
    scope = circuits.identity(ctx)
    permissions = sorted({name, *child_tools})
    principal = 'posix:' + str(os.getuid())
    issuer = 'local-posix-owner:' + str(os.getuid())
    now = time.time()
    record = {'verified': True, 'readback_confirmed': True,
        'principal_id': principal, 'mission_id': ctx['mission_id'], 'task_id': ctx['task_id'],
        'action': name.upper(), 'source_hash': scope['source_sha'],
        'scope_sha256': circuits.digest(arguments), 'issued_at': now, 'expires_at': now + 60,
        'evidence_ref': 'queue-store:local-posix-owner-approval',
        'kernel_uid': os.getuid(), 'permissions': permissions,
        'remote_authentication': False}
    ref = '.ssh-approval-' + circuits.digest(record)[:24]
    with ctx['store']._locked():
        ctx['store'].save_checkpoint(ref, 'KMCW_POSIX_OWNER_APPROVAL_V1', record)
        observed = ctx['store'].load_checkpoint(ref)
        if observed is None or observed.get('data') != record:
            raise ToolError('SSH_APPROVAL_READBACK_FAILED')
    binding = {'qualified': True, 'account_id': ctx['account_id'],
        'mission_id': ctx['mission_id'], 'task_id': ctx['task_id'],
        'source_sha': scope['source_sha'], 'arguments': copy.deepcopy(arguments),
        'write_authorized': True, 'approval_ref': ref}
    role = {'role': 'OWNER', 'permissions': permissions, 'account_id': ctx['account_id'],
            'mission_ids': [ctx['mission_id']], 'task_ids': [ctx['task_id']]}
    call_ctx = {**ctx, 'source_sha': scope['source_sha'],
        'command_auth': {'channel': CHANNEL, 'method': METHOD, 'issuer': issuer,
            'authenticated': True, 'role': 'OWNER', 'principal_id': principal,
            'permissions': permissions, 'account_id': ctx['account_id'],
            'mission_id': ctx['mission_id'], 'task_ids': [ctx['task_id']],
            'issued_at': now, 'expires_at': now + 60},
        'command_channels': {CHANNEL: {'qualified': True, 'method': METHOD,
            'issuer': issuer, 'principals': {principal: role}}},
        'tool_bindings': {name: binding}, 'command_approvals': {ref: record}}
    # The same MCP call path checks exact arguments, policy and approval, then
    # records the actual tool audit in the original QueueStore.
    from .mcp_server import _call
    return _call(registry, {'name': name, 'arguments': arguments}, call_ctx)


def execute(path, graph, command, request_id, *, approved=False, max_steps=8):
    ctx, registry = load_owner_runtime(path)
    compiled = circuits.compile_command({'command': command, 'request_id': request_id,
                                         'graph': graph}, ctx, registry)
    tools = sorted({node['tool'] for node in compiled['manifest']['graph']['nodes']})
    if not approved:
        return {'status': 'PREVIEW_ONLY', 'execution_authority': False, **compiled}
    submitted = invoke(ctx, registry, 'kmcp.circuit.submit',
                       {'manifest': compiled['manifest']}, child_tools=tools, approved=True)
    run = invoke(ctx, registry, 'kmcp.circuit.run',
                 {'circuit_id': submitted['circuit_id'],
                  'manifest_sha256': submitted['manifest_sha256'], 'max_steps': max_steps},
                 child_tools=tools, approved=True)
    return {'schema': 'KMCW_POSIX_CIRCUIT_RESULT_V1', 'authentication': METHOD,
            'remote_admin_authority': False, 'result': run,
            'report': circuits.report({'circuit_id': submitted['circuit_id']}, ctx)}
