#!/usr/bin/env python3
"""Real owner-authorized P01 -> D01 -> D02 canary, original queue and PostgreSQL.
No Canon access, resource creation, arbitrary SQL, service or HTTP cutover.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from coding_worker_v2.agent.owner_cli import execute, load_owner_runtime
from coding_worker_v2.agent.kmco_route_audit import audit_routes
from coding_worker_v2.agent.tools import ToolError
from coding_worker_v2.agent.native_database import TARGET, OPERATION


def build_graph(file, quote):
    obj = {'type': 'object'}
    return {'nodes': [
        {'id': 'source', 'tool': 'kmco.p01.evidence', 'arguments': {'file': file, 'quote': quote},
         'after': [], 'result_schema': obj},
        {'id': 'checkpoint', 'tool': 'kmco.p07.checkpoint', 'arguments': {
            'checkpoint_id': 'native-database-canary', 'observation': {
                'phase':'source-evidence','progress_units':1,'evidence_refs':['native:P01'],
                'failure_class':'NONE','terminal':False}},
         'after':['source'],'result_schema':obj},
        {'id':'memory','tool':'kmco.p08.memory','arguments':{
            'goal':'Verify native KMCO scoped database read','limit':5},
         'after':['checkpoint'],'result_schema':obj},
        {'id': 'owner', 'tool': 'kmco.d01.authority', 'arguments': {
            'system': 'postgres', 'target_id': TARGET, 'operation': OPERATION},
         'after': ['memory'], 'result_schema': {'type':'object','properties':{
            'authority_ref':{'type':'string'}},'required':['authority_ref']}},
        {'id': 'read', 'tool': 'kmco.d02.read', 'arguments': {
            'system': 'postgres', 'target_id': TARGET, 'operation': OPERATION, 'limit': 20},
         'after': ['owner'], 'result_schema': {'type':'object','properties':{
            'readback':{'type':'object'},'status':{'type':'string'}},'required':['readback','status']}}],
        'outputs': {'authority': {'$ref': 'owner#/authority_ref'},
                    'readback': {'$ref': 'read#/readback'},
                    'status': {'$ref': 'read#/status'}},
        'acceptance': [{'path': '/status', 'schema': {
            'type': 'string', 'enum': ['READ_EXECUTED_UNVERIFIED']}}]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--receipt', required=True)
    args = ap.parse_args()
    if os.environ.get('RUNNER_NAME') != 'ubuntu-usb':
        raise SystemExit('EXACT_RUNNER_REQUIRED')
    cfg = Path.home()/'.config/knowingmind/kmcw-owner-runtime.json'
    report = {'schema': 'KMCO_NATIVE_DATABASE_CANARY_V1', 'source_sha': os.environ['SOURCE_SHA'],
              'observed_at': datetime.now(timezone.utc).isoformat(), 'node': 'ubuntu-usb',
              'status': 'NOT_RUN', 'production_acceptance': 'NOT_GRANTED',
              'independent_qc': 'NOT_RUN', 'http_acceptance': 'NOT_TESTED'}
    try:
        ctx, reg = load_owner_runtime(cfg)
        coverage = audit_routes(ctx['kmco_catalog'], reg, ctx)
        report['native_source_bound'] = coverage['native_operation_source_bound']
        report['remaining_design_only'] = coverage['design_only_operations']
        file = 'av15/coding_worker_v2/README.md'
        quote = next(line.strip() for line in (Path(ctx['workspace'])/file).read_text().splitlines()
                     if line.strip())[:500]
        graph = build_graph(file, quote)
        # Same canonical request ID on retries: original queue dedup prevents replay.
        result = execute(cfg, graph, 'Verify native KMCO scoped database read',
                         'kmco-native-db-'+os.environ['GITHUB_RUN_ID'], approved=True)
        if result['result']['status'] != 'COMPLETED':
            report['node_status'] = {k: {'status':v['status'], 'error':v.get('error')}
                                     for k,v in result['report']['nodes'].items()}
            raise ToolError('KMCO_NATIVE_DATABASE_CIRCUIT_NOT_COMPLETED')
        rows = result['report']['outputs']['readback']['data']['rows']
        if not any(row.get('operation') == 'kmco.p01.evidence' for row in rows):
            raise ToolError('KMCO_NATIVE_PERSISTED_P01_ROW_NOT_READ_BACK')
        report['readback_row_count'] = len(rows)
        receipt = result['report']['receipt']
        path = Path(receipt['uri'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != receipt['sha256']:
            raise ToolError('KMCO_NATIVE_RECEIPT_DIGEST_MISMATCH')
        # Original report retains circuit outputs. Never copy row values to logs.
        report.update(status='NATIVE_CIRCUIT_COMPLETED_UNVERIFIED',
                      authentication=result['authentication'], remote_admin_authority=False,
                      circuit_id=result['report']['circuit_id'], receipt=receipt)
        # Anonymous and unbound calls must still be denied in the real registry.
        try:
            reg.call('kmco.d02.read', {'system': 'postgres', 'target_id': TARGET,
                                     'operation': OPERATION}, ctx, mcp=True)
        except ToolError as exc:
            if exc.reason != 'MCP_TOOL_SCOPE_DENIED':
                raise
            report['unauthorized_deny'] = 'PASS'
        else:
            raise ToolError('KMCO_NATIVE_UNAUTHORIZED_CALL_ACCEPTED')
        report['runner_env_verifier_key_configured'] = bool(
            os.environ.get('KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY'))
    except Exception as exc:
        report['status'] = 'BLOCKED'
        report['blocker'] = exc.reason if isinstance(exc, ToolError) else type(exc).__name__
        raise
    finally:
        Path(args.receipt).write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
