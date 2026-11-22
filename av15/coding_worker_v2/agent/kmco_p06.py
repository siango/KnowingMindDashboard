"""P06-M01..M05: existing registry schema/grant/scope preflight, no dispatch."""
from .capabilities import authorized
from .sandbox import policy_decision
from .tools import ToolError


def run_p06(registry, name, arguments, ctx):
    # M01: exact existing capability, no arbitrary function or URL resolution.
    contracts = {s['name']: s for s in registry.command_contract()['commands']}
    spec = contracts.get(name)
    if spec is None or name.startswith('kmco.p06.'):
        raise ToolError('P06_CAPABILITY_UNKNOWN_OR_RECURSIVE')
    # M02: use the same validator that ToolRegistry.call executes.
    registry.validate_arguments(name, arguments)
    # M03: same issuer, task, SHA, exact arguments and revocation gate.
    if not authorized(spec, ctx, arguments):
        raise ToolError('P06_GRANT_DENIED')
    # M04: preserve local scope policy even after a valid grant.
    if ctx.get('read_only') and spec['permission'] != 'read':
        raise ToolError('P06_READONLY_DENIED')
    # Privileged preflight is deliberately not advertised as mutation approval.
    if spec['permission'] != 'read':
        raise ToolError('P06_PRIVILEGED_DISPATCH_REQUIRES_ORIGINAL_TOOL')
    from pathlib import Path
    paths = []
    if arguments.get('repo'):
        try:
            paths = [str(Path(arguments['repo']).resolve().relative_to(
                Path(ctx.get('workspace', '')).resolve()))]
        except ValueError:
            raise ToolError('P06_SCOPE_DENIED') from None
    decision = policy_decision('test' if name == 'test_run' else 'inspect',
                              ctx['task_id'], ctx.get('workspace', ''),
                              paths=paths, command=arguments.get('command', ''),
                              actor='KMCW_TOOL_ROUTER')
    if decision['decision'] != 'AUTO_APPROVE':
        raise ToolError('P06_SCOPE_DENIED')
    # M05: output has no target result/credentials and is never a reusable grant.
    return {'circuit': 'P06', 'steps_completed': [f'P06-M{i:02d}' for i in range(1, 6)],
            'status': 'READ_PREFLIGHT_PASS_NOT_EXECUTED', 'tool': name,
            'registry_sha256': registry.command_contract()['sha256'],
            'dispatched': False, 'authority_granted': False,
            'independent_qc': 'NOT_RUN'}
