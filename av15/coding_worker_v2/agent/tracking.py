"""Read-only lifecycle projection over existing QueueStore/receipts, never a SSOT."""
from __future__ import annotations
import hashlib
import math
import json
import time
from pathlib import Path
from . import memory
from .executor import completion_readback, namespaced_id, plan_readiness

FIELDS = ('Mission', 'Task DAG', 'Owner Node', 'Provider', 'Capability', 'Heartbeat',
          'Lease', 'ACK', 'Execution', 'QC', 'Receipt', 'Cost', 'Memory', 'Checkpoint', 'Freshness')


def fresh(observation, *, now, max_age=60):
    if not isinstance(observation, dict):
        return False
    stamp, until = observation.get('observed_at'), observation.get('freshness_until')
    return (all(type(v) in (int, float) and math.isfinite(v) for v in (stamp, until))
            and 0 <= now - stamp <= max_age and now < until <= stamp + max_age
            and observation.get('authenticated') is True
            and bool(observation.get('source_ref')) and bool(observation.get('principal_id')))


def snapshot(plan, ctx, *, now=None):
    from .pilot import usage
    now = time.time() if now is None else now
    store = ctx['store']
    source = memory.current_base_commit(ctx['workspace'])
    readiness = plan_readiness(plan, store, ctx['receipts_dir'], ctx['workspace'],
                               account_id=ctx.get('account_id'))
    runtime = ctx.get('tracking_runtime', {})  # existing trusted middleware only
    runtime_fresh = (fresh(runtime, now=now) and runtime.get('online') is True
                     and runtime.get('owner_node') == plan.owner_node
                     and runtime.get('mission_id') == plan.mission_id
                     and runtime.get('source_sha') == source)
    rows = []
    for task in plan.tasks:
        identity = namespaced_id(plan, task.task_id)
        proof = completion_readback(plan, task.task_id, store, ctx['receipts_dir'],
                                    ctx['workspace'], account_id=ctx.get('account_id'))
        events = store.read_events(identity)
        if any(e.get('mission_id') not in (None, plan.mission_id) for e in events['events']):
            raise ValueError('TRACKING_EVENT_IDENTITY_MISMATCH')
        checkpoint = store.load_checkpoint(identity)
        leased = store.lease_valid(identity)
        node = next(n for n in readiness['nodes'] if n['task_id'] == task.task_id)
        queue_refs = []
        for queued in (store.inbox / f'{identity}.json', store.doing / f'{identity}.json',
                       store.done / identity / 'task.json', store.failed / identity / 'task.json'):
            if queued.is_file():
                if queued.stat().st_size > 1_000_000:
                    raise ValueError('TRACKING_TASK_TOO_LARGE')
                raw_task = json.loads(queued.read_bytes())
                if (raw_task.get('mission_id') != plan.mission_id or raw_task.get('task_id') != identity
                        or raw_task.get('owner_node') != plan.owner_node
                        or raw_task.get('input', {}).get('hints', {}).get('account_id') != ctx.get('account_id')):
                    raise ValueError('TRACKING_QUEUE_IDENTITY_MISMATCH')
                queue_refs.append({'uri':str(queued),'sha256':hashlib.sha256(queued.read_bytes()).hexdigest()})
        path = Path(ctx['receipts_dir']) / f'{identity}.json'
        receipt, cost = None, None
        if path.is_file():
            from ..receipts import read_receipt
            receipt = read_receipt(path)
            if (receipt['mission_id'] != plan.mission_id or receipt['task_id'] != identity
                    or receipt['input'].get('hints', {}).get('account_id') != ctx.get('account_id')):
                raise ValueError('TRACKING_RECEIPT_IDENTITY_MISMATCH')
            cost = usage(receipt)
        # A registration or an event containing RUNNING/DONE is not live proof.
        if not events['chain_intact']:
            status = 'BLOCKED'
            node = {**node, 'reason': 'EVENT_CHAIN_INTEGRITY_FAILED'}
        elif proof['verified'] and proof['acceptance_passed'] == proof['acceptance_total']:
            status = 'DONE'
        elif receipt and receipt['status'] in {'FAILED', 'BLOCKED', 'QC_PENDING'}:
            status = 'QC' if receipt.get('next_action') == 'AWAIT_INDEPENDENT_VERIFICATION' else 'BLOCKED'
        elif node['decision'] in {'BLOCKED', 'MONITOR'} or node['reason'] == 'RETAIN_BLOCKED_CHECKPOINT':
            status = 'BLOCKED'
        else:
            status = 'QUEUED' if (store.inbox / f'{identity}.json').is_file() else 'UNAVAILABLE'
        rows.append({'task_id': identity, 'local_task_id': task.task_id, 'deps': task.deps,
                     'owner_node': plan.owner_node, 'source_sha': source, 'status': status,
                     'provider': (receipt or {}).get('output', {}).get('provider'),
                     'capability': 'NOT_LIVE_QUALIFIED',
                     'heartbeat': {'fresh': bool(runtime_fresh), 'source_ref': runtime.get('source_ref')},
                     'lease': {'local_valid': leased, 'central_fence': 'UNVERIFIED'},
                     'ack': 'NOT_OBSERVED', 'execution': {'dispatchable': False,
                         'running_verified': False, 'reason': node['reason']},
                     'qc': 'INDEPENDENT_VERIFIED' if proof['verified'] else 'NOT_VERIFIED',
                     'receipt': proof, 'cost': cost,
                     'memory': memory.planning_lessons(task.goal, ctx['workspace'], source, store,
                          ctx['receipts_dir'], account_id=ctx.get('account_id'), plan=plan),
                     'checkpoint': checkpoint, 'event_count': events['event_count'],
                     'event_chain_intact': events['chain_intact'], 'spool_sha256': events['spool_sha256'],
                     'event_hashes': [e['event_hash'] for e in events['events']]})
        plan_path = Path(ctx['state_dir']) / 'plans' / f'{plan.plan_id}.json'
        rows[-1]['lifecycle'] = {
            'PLAN': {'uri':str(plan_path),'sha256':hashlib.sha256(plan_path.read_bytes()).hexdigest() if plan_path.is_file() else None},
            'TASK': queue_refs,
            'EXECUTION': {'event_spool_sha256':events['spool_sha256'],'candidate_content_sha256':
                          (receipt or {}).get('output', {}).get('candidate_content_sha256')},
            'QC': {'independent_verified':proof['verified'], 'next_action':(receipt or {}).get('next_action')},
            'RECEIPT': {'uri':proof['receipt'],'sha256':proof.get('sha256'),'status':(receipt or {}).get('status')},
            'MEMORY': {'backend':'EXISTING_RECEIPT_AND_EVENT_LEARNING','central_sync':'UNSYNCED'},
            'STATUS': {'status':status,'dispatchable':False}}
    all_done = all(r['status'] == 'DONE' for r in rows)
    return {'mission_id': plan.mission_id, 'plan_id': plan.plan_id, 'source_sha': source,
            'status': 'DONE' if all_done else 'ADMIT_BLOCKED', 'tasks': rows,
            'authority': {'local': 'EXISTING_QUEUESTORE_AND_SIGNED_RECEIPTS',
                          'project_truth': 'DHAMMA_POSTGRESQL_PROJECTION_ONLY',
                          'D1': 'CURRENT_AUTHENTICATED_TASK_SCOPED_INTERFACE_UNAVAILABLE'},
            'permissions': {'local_read': True, 'local_checkpoint': True,
                            'central_write': False, 'central_execution_read': 'UNQUALIFIED'},
            'coverage': {field: ('LOCAL_EVIDENCE' if field in {'Mission','Task DAG','Owner Node',
                         'Execution','QC','Receipt','Memory','Checkpoint'} else 'PARTIAL_OR_MISSING')
                         for field in FIELDS},
            'missing_fields': ['current_D1_principal_ACL', 'central_fencing', 'native_ACK',
                               'qualified_live_capability', 'fresh_authenticated_heartbeat', 'known_quota'],
            'admit': 'ADMIT_BLOCKED', 'central_sync': 'UNSYNCED', 'kmnw': 'PREVIEW_ONLY',
            'serena': 'NOT_QUALIFIED', 'projection_only': True, 'dispatchable': False}


def project_runtime_view(payload, provenance, *, now=None):
    """Normalize the existing Dhamma projection contract, never forward D1 calls."""
    import json
    from datetime import datetime
    now = time.time() if now is None else now
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if (len(raw) > 100_000 or provenance.get('authenticated') is not True
            or provenance.get('body_sha256') != hashlib.sha256(raw).hexdigest()
            or provenance.get('tool') != 'project_federated_runtime_readback'
            or not isinstance(payload, dict) or not isinstance(payload.get('runtimes'), list)
            or len(payload['runtimes']) > 50):
        raise ValueError('PROJECTION_PROVENANCE_UNVERIFIED')
    rows = []
    for row in payload['runtimes']:
        def timestamp(value):
            try:
                return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
            except (ValueError, TypeError):
                return None
        observation = {'observed_at': timestamp(row.get('observed_at')),
                       'freshness_until': timestamp(row.get('freshness_until')),
                       'source_ref': row.get('source_ref'), 'principal_id': provenance.get('tool'),
                       'authenticated': True}
        live = fresh(observation, now=now)
        rows.append({'runtime_id': row.get('runtime_id'), 'node_id': row.get('node_id'),
                     'source_ref': row.get('source_ref'), 'observed_at': row.get('observed_at'),
                     'freshness_until': row.get('freshness_until'), 'fresh': live,
                     'reported_state': row.get('current_state'),
                     'status': 'FRESH_PROJECTION_ONLY' if live else 'STALE_OR_UNQUALIFIED',
                     'dispatchable': False, 'execution_authority': 'NOT_GRANTED_BY_PROJECTION'})
    return {'project_id': payload.get('project_id'), 'runtimes': rows,
            'body_sha256': provenance['body_sha256'], 'projection_only': True,
            'project_truth_authority': payload.get('project_truth_authority'),
            'central_sync': 'UNSYNCED', 'D1_execution_truth': 'SEPARATE_UNQUALIFIED_INTERFACE'}
