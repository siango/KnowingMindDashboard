"""Installable, host-neutral KMCP launcher. Credentials are never JSON config."""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from ..config import WorkerConfig
from ..queue_store import QueueStore
from .tools import ToolError, build_default_registry
from . import circuits


def load_runtime(path):
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > 65536:
        raise ToolError('KMCP_CONFIG_TOO_LARGE')
    config = json.loads(path.read_text('utf-8'))
    required = {'schema', 'node_id', 'account_id', 'mission_id', 'task_id', 'workspace', 'runtime_base'}
    if (not isinstance(config, dict) or not required.issubset(config)
            or set(config) - required - {'kmco_catalog', 'pilot_config', 'poll_tools', 'cloudflare', 'postgres_service'}
            or config['schema'] != 'KMCP_RUNTIME_V1'
            or any(not isinstance(config[k], str) or not config[k].strip() for k in required)):
        raise ToolError('KMCP_CONFIG_INVALID')
    def relative(value):
        target = Path(value).expanduser()
        return target.resolve() if target.is_absolute() else (path.parent / target).resolve()
    workspace = relative(config['workspace'])
    if not (workspace / '.git').exists():
        raise ToolError('KMCP_GIT_WORKSPACE_REQUIRED')
    base = relative(config['runtime_base'])
    worker = replace(WorkerConfig.from_env(), owner_node=config['node_id'])
    context = {'workspace': str(workspace), 'store': QueueStore(base / 'queue'),
               'receipts_dir': base / 'receipts', 'state_dir': base / 'state',
               'owner_node': config['node_id'], 'account_id': config['account_id'],
               'mission_id': config['mission_id'], 'task_id': config['task_id'],
               'config': worker, 'evidence': []}
    for directory in (context['receipts_dir'], context['state_dir'] / 'plans'):
        directory.mkdir(parents=True, exist_ok=True)
    if 'pilot_config' in config:
        from .pilot import load_context
        pilot = load_context(relative(config['pilot_config']), worker_config=worker)
        if (pilot['workspace'] != context['workspace'] or pilot['store'].base != context['store'].base
                or pilot['account_id'] != context['account_id'] or pilot['mission_id'] != context['mission_id']):
            raise ToolError('KMCP_PILOT_SCOPE_MISMATCH')
        context.update(pilot)
        context['owner_node'] = config['node_id']
        context['task_id'] = config['task_id']
    catalog = None
    if 'kmco_catalog' in config:
        from .kmco_runtime import KMCOCatalog
        catalog = KMCOCatalog.from_snapshot(relative(config['kmco_catalog']))
    poll = config.get('poll_tools', [])
    if not isinstance(poll, list) or any(p != 'pilot_reconcile' for p in poll):
        raise ToolError('KMCP_POLL_POLICY_INVALID')
    context['kmcp_poll_tools'] = tuple(poll)
    cloudflare = None
    if 'cloudflare' in config:
        from .native_cloudflare import native_client
        # Provider credentials and roles never come from the JSON document.
        from .native_cloudflare import private_file
        private_file(path)
        cloudflare = native_client(config['cloudflare'])
    if 'postgres_service' in config:
        from .kmco_usage import SERVICE_RE
        service = config['postgres_service']
        if not isinstance(service, str) or not SERVICE_RE.fullmatch(service):
            raise ToolError('KMCP_POSTGRES_SERVICE_INVALID')
        context['kmco_pg_service'] = service
    registry = build_default_registry(kmco_catalog=catalog, circuits=True, cloudflare=cloudflare)
    context['tool_registry'] = registry
    if catalog is not None:
        context['kmco_catalog'] = catalog
    return context, registry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('catalog')
    sub.add_parser('local-check')
    sub.add_parser('route-audit')
    plan = sub.add_parser('plan')
    plan.add_argument('--command', required=True)
    plan.add_argument('--request-id', required=True)
    plan.add_argument('--graph', help='Host-AI decomposition JSON; omitted uses configured local planner')
    for name in ('status', 'report', 'cancel'):
        sub.add_parser(name).add_argument('circuit_id')
    serve = sub.add_parser('serve')
    serve.add_argument('--admin-runtime-context', action='store_true')
    args = parser.parse_args(argv)
    try:
        ctx, registry = load_runtime(args.config)
        if args.action == 'serve':
            from .mcp_server import serve
            from .capabilities import KMAdminContextProducer
            return serve(ctx, context_provider=KMAdminContextProducer.runtime_context
                         if args.admin_runtime_context else None)
        if args.action == 'route-audit':
            from .kmco_route_audit import audit_routes
            from .kmco_runtime import KMCOError
            if ctx.get('kmco_catalog') is None:
                raise ToolError('KMCO_CATALOG_NOT_BOUND')
            try:
                report = audit_routes(ctx['kmco_catalog'], registry, ctx)
            except KMCOError as exc:
                raise ToolError(exc.code) from None
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.action == 'local-check':
            from .local_mcp_acceptance import inspect_local
            print(json.dumps(inspect_local(ctx, registry), ensure_ascii=False, indent=2))
            return 0
        if args.action == 'catalog':
            value = registry.command_contract()
        elif args.action == 'plan':
            request = {'command': args.command, 'request_id': args.request_id}
            if args.graph:
                file = Path(args.graph)
                if file.stat().st_size > circuits.LIMIT:
                    raise ToolError('CIRCUIT_PAYLOAD_LIMIT')
                request['graph'] = json.loads(file.read_text('utf-8'))
            value = circuits.compile_command(request, ctx, registry)
        else:
            # Read/status/cancel local OS-owner interface; execution is ONLY via
            # the original authenticated registry, never a JSON principal.
            value = (circuits.cancel if args.action == 'cancel' else circuits.report)(
                {'circuit_id': args.circuit_id}, ctx)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ToolError, OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'reason': exc.reason if isinstance(exc, ToolError)
                          else type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
