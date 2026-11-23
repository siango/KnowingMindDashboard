"""Existing stdio MCP with bounded POSIX-owner KMCO reads; no remote grants."""
from __future__ import annotations
import argparse
import os
import time
from pathlib import Path
from .tools import ToolError
from .owner_cli import load_owner_runtime, CHANNEL, METHOD
from .memory import current_base_commit

# Only deterministic bounded reads. All writes and other tools retain original ACLs.
READ_TOOLS = frozenset({'kmco.inspect', 'kmco.plan', 'kmco.p01.evidence',
                        'kmco.p02.resume', 'kmco.p03.loop_guard', 'kmco.p04.plan'})


class LocalOwnerRegistry:
    def __init__(self, config, context, registry):
        self.config = Path(config).expanduser().resolve(strict=True)
        self.context = context
        self.registry = registry

    def __getattr__(self, name):
        return getattr(self.registry, name)

    def call(self, name, arguments, ctx, *, mcp=False):
        if not mcp or name not in READ_TOOLS:
            return self.registry.call(name, arguments, ctx, mcp=mcp)
        # Recheck kernel/config/queue/workspace ownership for every call.
        fresh, _ = load_owner_runtime(self.config)
        for key in ('account_id', 'mission_id', 'task_id', 'workspace', 'owner_node'):
            if fresh.get(key) != self.context.get(key) or ctx.get(key) != fresh.get(key):
                raise ToolError('LOCAL_OWNER_SCOPE_CHANGED')
        if fresh['store'].base != self.context['store'].base:
            raise ToolError('LOCAL_OWNER_SCOPE_CHANGED')
        if os.getuid() == 0 or fresh['account_id'] != 'posix:' + str(os.getuid()):
            raise ToolError('SSH_NATIVE_OWNER_IDENTITY_REQUIRED')
        spec = next(s for s in self.registry.command_contract()['commands'] if s['name'] == name)
        if spec['permission'] != 'read':
            raise ToolError('LOCAL_OWNER_WRITE_DENIED')
        self.registry.validate_arguments(name, arguments)
        source_sha = current_base_commit(fresh['workspace'])
        permissions = sorted({name, *spec['required_permissions']})
        now = time.time()
        principal = 'posix:' + str(os.getuid())
        issuer = 'local-posix-owner:' + str(os.getuid())
        role = {'role': 'OWNER', 'permissions': permissions,
                'account_id': fresh['account_id'], 'mission_ids': [fresh['mission_id']],
                'task_ids': [fresh['task_id']]}
        binding = {'qualified': True, 'source_sha': source_sha,
                   'arguments': arguments, 'write_authorized': False,
                   **{k: fresh[k] for k in ('account_id', 'mission_id', 'task_id')}}
        scoped = {**ctx, 'read_only': True,
                  'command_auth': {'channel': CHANNEL, 'method': METHOD, 'issuer': issuer,
                      'authenticated': True, 'role': 'OWNER', 'principal_id': principal,
                      'permissions': permissions, 'account_id': fresh['account_id'],
                      'mission_id': fresh['mission_id'], 'task_ids': [fresh['task_id']],
                      'issued_at': now, 'expires_at': now + 30},
                  'command_channels': {CHANNEL: {'qualified': True, 'method': METHOD,
                      'issuer': issuer, 'principals': {principal: role}}},
                  'tool_bindings': {name: binding}}
        # Original exact-argument, source-SHA, policy, handler and usage audit checks.
        return self.registry.call(name, arguments, scoped, mcp=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    ctx, registry = load_owner_runtime(args.config)
    ctx['tool_registry'] = LocalOwnerRegistry(args.config, ctx, registry)
    from .mcp_server import serve
    return serve(ctx)


if __name__ == '__main__':
    raise SystemExit(main())
