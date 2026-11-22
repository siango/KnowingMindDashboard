"""Read-only KVES projection of the original typed registry and circuit traces."""
from __future__ import annotations
from . import circuits


def projection(ctx, registry, *, circuit_id=None):
    contract = registry.command_contract()
    trace = circuits.report({'circuit_id': circuit_id}, ctx) if circuit_id else None
    nodes = [{
        'id': row['name'], 'label': row['capability'], 'kind': 'REGISTERED_TOOL',
        'input_schema': row['input_schema'], 'output_schema': row['output_schema'],
        'permission': row['permission'], 'source_ref': row['source_ref'],
        'backend_ref': row['backend_ref'], 'side_effects': row['side_effects'],
        'registered': True, 'live_readiness': 'NOT_PROBED_BY_PROJECTION',
        'execution_authority': False,
    } for row in contract['commands']]
    edges = []
    if circuit_id:
        # Original checkpoint validates digest and account/mission identity.
        body = circuits._load(circuit_id, ctx)
        graph = body['manifest']['graph']
        nodes = [{**node, 'kind': 'CIRCUIT_STEP',
                  'trace': trace['nodes'][node['id']], 'execution_authority': False}
                 for node in graph['nodes']]
        edges = [{'source': source, 'target': node['id'], 'kind': 'DECLARED_DEPENDENCY'}
                 for node in graph['nodes'] for source in node['after']]
    return {'schema': 'KVES_REGISTRY_PROJECTION_V1', 'mode': 'READ_ONLY',
            'source_sha': circuits.identity(ctx)['source_sha'],
            'runtime_sha256': circuits.runtime_digest(), 'registry_sha256': contract['sha256'],
            'nodes': nodes, 'edges': edges, 'trace': trace,
            'ui_deployed': False, 'execution_authority': False,
            'independent_qc': 'NOT_ATTESTED_BY_PROJECTION'}
