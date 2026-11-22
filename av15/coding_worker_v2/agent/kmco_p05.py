"""P05 exposes the original KMCW completion readback, not another verifier.

The plan is data. Existing issuer-bound identity, receipts, queue, source SHA,
request/proof signatures and criterion evidence remain the acceptance gates.
"""
from pathlib import Path

from ..receipts import read_receipt
from .planner import validate_plan_dict, PlanError
from .executor import completion_readback, namespaced_id
from .memory import receipt_verified
from .tools import ToolError


def run_p05(raw_plan, ctx):
    try:
        plan = validate_plan_dict(raw_plan)
    except PlanError as exc:
        raise ToolError(exc.reason) from None
    if (plan.mission_id != ctx.get('mission_id')
            or plan.owner_node != ctx.get('owner_node')
            or not ctx.get('account_id') or ctx.get('store') is None
            or not ctx.get('receipts_dir') or not ctx.get('workspace')):
        raise ToolError('P05_RUNTIME_CONTEXT_REQUIRED')
    tasks = [t for t in plan.tasks if namespaced_id(plan, t.task_id) == ctx.get('task_id')]
    if len(tasks) != 1:
        raise ToolError('P05_TASK_SCOPE_DENIED')
    planned = tasks[0]
    # M01 Artifact Checker: choose only this issuer-scoped existing receipt.
    path = Path(ctx['receipts_dir']) / f"{ctx['task_id']}.json"
    try:
        receipt = read_receipt(path)
        # M02 Test Result Reader: original QC gate; model DONE cannot substitute.
        if not receipt_verified(receipt):
            raise ToolError('P05_TEST_OR_QC_EVIDENCE_REQUIRED')
        # M03 Evidence Digest + M04 Independent Proof Checker: original reader
        # hashes receipt bytes, checks history/terminal conflict, verifies the
        # original signature and candidate SHA, then compares exact task scope.
        result = completion_readback(plan, planned.task_id, ctx['store'],
            ctx['receipts_dir'], ctx['workspace'], account_id=ctx['account_id'])
    except (OSError, ValueError, KeyError, TypeError):
        raise ToolError('P05_ARTIFACT_INVALID') from None
    # M05 Completion Gate requires individual acceptance evidence as well.
    if (result.get('verified') is not True
            or result.get('acceptance_total', 0) < 1
            or result.get('acceptance_passed') != result.get('acceptance_total')):
        raise ToolError('P05_COMPLETION_NOT_VERIFIED')
    return {'circuit': 'P05', 'steps_completed': [f'P05-M{i:02d}' for i in range(1, 6)],
            'status': 'COMPLETION_EVIDENCE_VALIDATED',
            'receipt_sha256': result['sha256'],
            'receipt_version': receipt['receipt_version'],
            'mission_id': plan.mission_id, 'task_id': ctx['task_id'],
            'acceptance_passed': result['acceptance_passed'],
            'acceptance_total': result['acceptance_total'],
            'authority_granted': False, 'production_acceptance': 'NOT_GRANTED',
            'task_status_changed': False}
