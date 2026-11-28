"""P07/P08 concrete checkpoint and scoped receipt-memory operations.

Reuses QueueStore checkpoints/locks and original receipt readers. No new queue,
vector store or source of truth; resume output never automatically repeats work.
"""
import hashlib
import json
import time
from pathlib import Path

from .memory import current_base_commit, _iter_receipts, work_case, normalize_goal
from ..tasks import FAILURE_CLASSES, ID_RE
from .tools import ToolError


def _scope(ctx):
    for field in ('mission_id', 'task_id', 'account_id', 'workspace'):
        if not isinstance(ctx.get(field), str) or not ctx[field]:
            raise ToolError('KMCO_CONTINUITY_SCOPE_REQUIRED')
    sha = current_base_commit(ctx['workspace'])
    if not sha or len(sha) != 40:
        raise ToolError('KMCO_CONTINUITY_SOURCE_REQUIRED')
    return sha


def run_p07(args, ctx):
    sha = _scope(ctx)
    store = ctx.get('store')
    if store is None:
        raise ToolError('P07_EXISTING_QUEUE_REQUIRED')
    ident, data = args['checkpoint_id'], args['observation']
    if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
        raise ToolError('P07_CHECKPOINT_ID_INVALID')
    if (not isinstance(data, dict) or set(data) != {'phase','progress_units','evidence_refs','failure_class','terminal'}
            or not isinstance(data['phase'], str) or not 1 <= len(data['phase']) <= 100
            or type(data['progress_units']) is not int or not 0 <= data['progress_units'] <= 1000000
            or type(data['terminal']) is not bool
            or not isinstance(data['failure_class'], str)
            or data['failure_class'] not in FAILURE_CLASSES | {'NONE'}
            or not isinstance(data['evidence_refs'], list) or len(data['evidence_refs']) > 20
            or any(not isinstance(x, str) or not 1 <= len(x) <= 500 for x in data['evidence_refs'])):
        raise ToolError('P07_CHECKPOINT_OBSERVATION_INVALID')
    payload = {'source_sha': sha, **{k: ctx[k] for k in ('mission_id','task_id','account_id')},
               'observation': data}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    key = '.kmco-p07-' + hashlib.sha256(json.dumps([
        ctx['account_id'],ctx['mission_id'],ctx['task_id'],ident]).encode()).hexdigest()
    # M01 writer: one immutable task-scoped checkpoint in original store.
    with store._locked():
        previous = store.load_checkpoint(key)
        if previous is None and (store.checkpoints/(key+'.json')).exists():
            raise ToolError('P07_CORRUPT_CHECKPOINT')
        reused = previous is not None
        if reused and previous.get('data') != {'payload': payload, 'sha256': digest}:
            raise ToolError('P07_IDEMPOTENCY_OR_SOURCE_CONFLICT')
        if not reused:
            store.save_checkpoint(key, 'KMCO_P07_CHECKPOINT_V1', {'payload': payload, 'sha256': digest})
        # M02 validator: read actual persisted bytes through the original reader.
        actual = store.load_checkpoint(key)
        if actual is None or actual.get('data') != {'payload': payload, 'sha256': digest}:
            raise ToolError('P07_CHECKPOINT_READBACK_FAILED')
    # M03 failure classification is observation, never diagnosed root cause.
    failure = data['failure_class']
    # M04 resume selector: terminal/unknown/security failures cannot auto-resume.
    blocked = data['terminal'] or failure in {'UNKNOWN','POLICY_DENY','LEASE_LOST','BUDGET_EXCEEDED'}
    # M05 resume packet refers to original checkpoint; no task dispatch occurs.
    return {'circuit': 'P07', 'steps_completed': [f'P07-M{i:02d}' for i in range(1,6)],
            'status': 'CHECKPOINT_READBACK_VERIFIED_RESUME_NOT_EXECUTED',
            'checkpoint_ref': 'queue-store:'+key, 'checkpoint_sha256': digest,
            'reused': reused, 'failure_class': failure,
            'resume_recommendation': 'BLOCKED' if blocked else 'RECHECK_CURRENT_AUTHORITY',
            'dispatched': False, 'authority_granted': False, 'independent_qc': 'NOT_RUN'}


def run_p08(args, ctx):
    # M01 exact truth owner: only existing local KMCW receipt domain.
    sha = _scope(ctx)
    root = ctx.get('receipts_dir')
    if root is None or not Path(root).is_dir():
        raise ToolError('P08_EXISTING_RECEIPT_OWNER_REQUIRED')
    goal = args['goal']; limit = args.get('limit', 5)
    if not isinstance(goal, str) or not 1 <= len(goal) <= 2000 or type(limit) is not int or not 1 <= limit <= 5:
        raise ToolError('P08_QUERY_LIMIT_INVALID')
    normalized = normalize_goal(goal)
    records = []
    # M02 exact goal key and M03 original lexical similarity; never pretend vector search.
    for examined, (path, receipt) in enumerate(_iter_receipts(Path(root)), start=1):
        # Charge every examined receipt, including corrupt, out-of-scope and
        # nonmatching records. A result limit is not a read budget.
        if examined > 1000:
            raise ToolError('P08_SCAN_BUDGET_EXCEEDED')
        if (not receipt or receipt.get('mission_id') != ctx['mission_id']
                or receipt.get('input',{}).get('hints',{}).get('account_id') != ctx['account_id']):
            continue
        old_goal = normalize_goal(receipt.get('input',{}).get('goal',''))
        exact = old_goal == normalized
        overlap = len(set(old_goal.split()) & set(normalized.split()))
        if not exact and not overlap:
            continue
        # M04 source/time freshness; similarity and receipt status do not grant truth.
        stamp = receipt.get('recorded_at')
        fresh = (type(stamp) in (int,float) and 0 <= time.time()-stamp <= 3600
                 and receipt.get('source_sha') == sha)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(((exact, fresh, overlap), {
            'task_id': receipt.get('task_id'), 'receipt_sha256': digest,
            'source_sha': receipt.get('source_sha'), 'receipt_status': receipt.get('status'),
            'freshness': 'CURRENT_SOURCE_OBSERVATION' if fresh else 'STALE',
            'classification': 'OBSERVED_REQUIRES_INDEPENDENT_READBACK',
            'authority': 'EXISTING_KMCW_RECEIPTS', 'retrieval_only': True}))
    # M05 deterministic bounded reconciliation, preserve original digest identity.
    unique = {}
    for score, row in sorted(records, key=lambda x: x[0], reverse=True):
        unique.setdefault(row['receipt_sha256'], row)
    return {'circuit': 'P08', 'steps_completed': [f'P08-M{i:02d}' for i in range(1,6)],
            'status': 'SCOPED_MEMORY_RETRIEVED_UNVERIFIED', 'cases': list(unique.values())[:limit],
            'truth_owner': 'EXISTING_KMCW_RECEIPTS', 'similarity_method': 'ORIGINAL_LEXICAL_OVERLAP',
            'authority_granted': False, 'independent_qc': 'NOT_RUN'}


def register_continuity(registry, catalog):
    from .tools import ToolSpec
    for group, name, fn, required, optional, permission in (
        ('P07','kmco.p07.checkpoint',run_p07,{'checkpoint_id':'str','observation':'dict'},{},'write'),
        ('P08','kmco.p08.memory',run_p08,{'goal':'str'},{'limit':'int'},'read')):
        def call(args, ctx, group=group, fn=fn):
            if any(f'{group}-M{i:02d}' not in catalog._operations for i in range(1,6)):
                raise ToolError('KMCO_CONTINUITY_CATALOG_INCOMPLETE')
            return fn(args, ctx)
        registry.register(ToolSpec(name, 'Existing checkpoint/scoped receipt memory', required, optional,
            permission, 'original QueueStore checkpoint only' if permission=='write' else 'none',
            'exact account/mission/task/source; no automatic replay or independent truth promotion'),call)
