"""Bounded self-inspection/proposal capability inside the existing task executor."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from . import memory
from .sandbox import policy_decision
from ..pipeline import test_argv

SCHEMA = 'KMCW_SELF_REPAIR_PROPOSAL_V1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def inspect(plan, task_id, ctx):
    from .executor import namespaced_id
    from .tools import build_default_registry
    task = next(t for t in plan.tasks if t.task_id == task_id)
    paths = sorted(set(task.allowed_paths + ctx['pilot']['context_paths']))
    files = {}
    for rel in paths:
        path = Path(ctx['workspace']) / rel
        if (policy_decision('inspect', task_id, ctx['workspace'], paths=[rel])['decision'] != 'AUTO_APPROVE'
                or path.is_symlink() or any(Path(ctx['workspace']).joinpath(*Path(rel).parts[:i]).is_symlink()
                for i in range(1, len(Path(rel).parts) + 1)) or not path.is_file()
                or path.stat().st_size > 200_000):
            raise ValueError('SELF_INSPECTION_SCOPE_DENIED')
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    event_path = ctx['store'].events / f'{namespaced_id(plan, task_id)}.jsonl'
    if event_path.is_file() and event_path.stat().st_size > 1_000_000:
        raise ValueError('SELF_INSPECTION_EVENT_HISTORY_TOO_LARGE')
    events = ctx['store'].read_events(namespaced_id(plan, task_id))
    registry = ctx.get('tool_registry') or build_default_registry()
    return {'mission_id': plan.mission_id, 'task_id': namespaced_id(plan, task_id),
            'source_sha': memory.current_base_commit(ctx['workspace']), 'source_files': files,
            'failure_history': [e for e in events['events'] if e['status'] in {'FAILED', 'BLOCKED', 'FAIL'}][-8:],
            'log_provenance': {'spool_sha256': events['spool_sha256'], 'chain_intact': events['chain_intact']},
            'checkpoint': ctx['store'].load_checkpoint(namespaced_id(plan, task_id)),
            'memory': memory.planning_lessons(task.goal, ctx['workspace'], memory.current_base_commit(ctx['workspace']),
                ctx['store'], ctx['receipts_dir'], account_id=ctx.get('account_id'), plan=plan,
                context=ctx),
            'tool_contract_sha256': registry.command_contract()['sha256'],
            'tool_qualification': {'serena': 'NOT_QUALIFIED', 'provider': 'REQUIRES_EXISTING_AUTHENTICATED_CAPABILITY_FEED'},
            'architectural_gaps': ['independent_promotion_QC', 'central_writer_readback', 'live_ingress_qualification'],
            'central_sync': 'UNSYNCED'}


def validate(proposal, allowed_paths, source_sha):
    required = {'schema', 'source_sha', 'source_files', 'edits', 'test_command', 'observation',
                'root_cause', 'rollback', 'max_attempts', 'proposal_sha256'}
    if (not isinstance(proposal, dict) or set(proposal) != required or proposal.get('schema') != SCHEMA
            or proposal['source_sha'] != source_sha or type(proposal['max_attempts']) is not int or proposal['max_attempts'] != 1
            or proposal['proposal_sha256'] != digest({k: v for k, v in proposal.items() if k != 'proposal_sha256'})
            or not isinstance(proposal['root_cause'], dict)
            or proposal['root_cause'].get('classification') != 'OBSERVED'
            or not proposal['root_cause'].get('evidence_refs')
            or not isinstance(proposal['edits'], list) or not 1 <= len(proposal['edits']) <= 3
            or not isinstance(proposal['source_files'], dict) or not 1 <= len(proposal['source_files']) <= 20):
        raise ValueError('SELF_REPAIR_PROPOSAL_INVALID')
    test_argv(proposal['test_command'])
    for rel, value in proposal['source_files'].items():
        if (not isinstance(rel, str) or Path(rel).is_absolute() or '..' in Path(rel).parts
                or not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)):
            raise ValueError('SELF_REPAIR_SNAPSHOT_INVALID')
    for edit in proposal['edits']:
        if (not isinstance(edit, dict) or set(edit) != {'path', 'old', 'new'}
                or edit['path'] not in allowed_paths or edit['path'] not in proposal['source_files']
                or not isinstance(edit['old'], str) or not edit['old'] or not isinstance(edit['new'], str)
                or len(edit['old']) + len(edit['new']) > 32_000):
            raise ValueError('SELF_REPAIR_PATCH_SCOPE_DENIED')
    observation = proposal['observation']
    if (not isinstance(observation, dict) or observation.get('rc') == 0
            or type(observation.get('rc')) is not int or not observation.get('output_sha256')
            or observation.get('source_files') != proposal['source_files']
            or observation.get('command') != proposal['test_command']):
        raise ValueError('SELF_REPAIR_FAILURE_EVIDENCE_REQUIRED')
    return proposal


def propose(plan, task_id, ctx, observation, edits, root_cause):
    inspection = inspect(plan, task_id, ctx)
    if inspection['log_provenance']['chain_intact'] is not True:
        raise ValueError('SELF_REPAIR_EVENT_EVIDENCE_CORRUPT')
    task = next(t for t in plan.tasks if t.task_id == task_id)
    # Observation is a retained local test artifact; no model assertion becomes evidence.
    ref = Path(observation['evidence_ref']).resolve()
    state = Path(ctx['state_dir']).resolve()
    if (not ref.is_relative_to(state) or ref.is_symlink() or not ref.is_file()
            or ref.stat().st_size > 100_000):
        raise ValueError('SELF_REPAIR_EVIDENCE_SCOPE_DENIED')
    retained = json.loads(ref.read_bytes())
    if retained != {k: v for k, v in observation.items() if k != 'evidence_ref'}:
        raise ValueError('SELF_REPAIR_EVIDENCE_READBACK_MISMATCH')
    if (not isinstance(observation.get('output'), str)
            or hashlib.sha256(observation['output'].encode()).hexdigest() != observation.get('output_sha256')):
        raise ValueError('SELF_REPAIR_FAILURE_OUTPUT_HASH_MISMATCH')
    for edit in edits:
        if (edit.get('path') not in task.allowed_paths
                or (Path(ctx['workspace']) / edit['path']).read_text().count(edit.get('old', '')) != 1):
            raise ValueError('SELF_REPAIR_PATCH_AMBIGUOUS_OR_OUT_OF_SCOPE')
    proposal = {'schema': SCHEMA, 'source_sha': inspection['source_sha'],
                'source_files': inspection['source_files'], 'edits': edits,
                'test_command': ctx['pilot']['test_command'], 'observation': observation,
                'root_cause': {'classification': 'OBSERVED', 'description': root_cause,
                               'evidence_refs': [str(ref), observation.get('output_sha256')]},
                'rollback': 'Retain active source; discard only independently owned clean candidate or reverse retained patch',
                'max_attempts': 1}
    proposal['proposal_sha256'] = digest(proposal)
    return validate(proposal, task.allowed_paths, inspection['source_sha'])


def tested_checkpoint_recoverable(plan, task, ctx, checkpoint):
    """Recognize completed candidate computation, never independent QC approval.

    Recovery may reclaim a lease, but cannot spend another edit/inference attempt.
    Every binding and file digest is checked before relaxing the retry budget.
    """
    from .executor import namespaced_id
    try:
        contract = task.execution_contract or {}
        proposal = contract.get('self_development')
        if not proposal or not checkpoint or checkpoint.get('stage') != 'tested':
            return False
        state = checkpoint['data']; snapshot = state['task_snapshot']; raw = snapshot['input']
        hints = raw['hints']; output = snapshot['output']; identity = namespaced_id(plan, task.task_id)
        source = memory.current_base_commit(ctx['workspace'])
        validate(proposal, task.allowed_paths, source)
        candidate = Path(raw['repo']).resolve()
        registry_path = Path(ctx['state_dir']) / 'worktrees.json'
        if not registry_path.is_file() or registry_path.stat().st_size > 1_000_000:
            return False
        entry = json.loads(registry_path.read_bytes()).get(identity, {})
        if (state.get('phase') != 'tested' or snapshot.get('status') != 'DONE'
                or snapshot.get('mission_id') != plan.mission_id or snapshot.get('task_id') != identity
                or snapshot.get('owner_node') != plan.owner_node or hints.get('account_id') != ctx.get('account_id')
                or hints.get('workspace') != ctx['workspace'] or hints.get('plan_id') != plan.plan_id
                or hints.get('base_commit') != source or state.get('source_sha') != source
                or hints.get('plan_digest') != digest(plan.to_dict())
                or hints.get('task_execution_contract') != contract
                or hints.get('approved_scope', {}).get('allowed_paths') != task.allowed_paths
                or raw.get('test_command') != ctx['pilot']['test_command']
                or entry.get('status') != 'active' or entry.get('task_id') != identity
                or entry.get('repo') != ctx['workspace']
                or entry.get('self_development_snapshot') != proposal['proposal_sha256']
                or Path(entry.get('workspace_path', '')).resolve() != candidate
                or candidate == Path(ctx['workspace']).resolve()
                or not candidate.is_relative_to((Path(ctx['state_dir']) / 'worktrees').resolve())
                or memory.current_base_commit(str(candidate)) != source
                or output.get('candidate_source_sha') != source):
            return False
        for rel, expected in proposal['source_files'].items():
            for root in (Path(ctx['workspace']), candidate):
                if (policy_decision('inspect', identity, str(root), paths=[rel])['decision'] != 'AUTO_APPROVE'
                        or any(root.joinpath(*Path(rel).parts[:i]).is_symlink()
                               for i in range(1, len(Path(rel).parts) + 1))):
                    return False
            if hashlib.sha256((Path(ctx['workspace']) / rel).read_bytes()).hexdigest() != expected:
                return False
        evidence = snapshot.get('evidence', [])
        tests_pass = any(e.get('event') == 'tests_run' and e.get('tests', {}).get('ran') is True
                         and e['tests'].get('rc') == 0 for e in evidence)
        syntax_pass = any(e.get('event') == 'syntax_checked' and e.get('syntax', {}).get('ok') is True
                          for e in evidence)
        return (tests_pass and syntax_pass
                and output.get('candidate_content_sha256') == memory.workspace_digest(str(candidate), list(proposal['source_files']))
                and state.get('workspace_digest') == memory.workspace_digest(str(candidate), task.allowed_paths))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def stage_candidate(proposal, task, active_workspace, registry):
    """Copy explicitly pinned overlay files only, never reset a dirty candidate."""
    entry = registry.get(task.task_id) if registry else None
    candidate = Path(task.task_input.repo).resolve()
    if (not entry or candidate != Path(entry['workspace_path']).resolve()
            or candidate == Path(active_workspace).resolve()
            or not candidate.is_relative_to(registry.roots.resolve())):
        raise ValueError('SELF_REPAIR_ISOLATED_CANDIDATE_REQUIRED')
    validate(proposal, task.task_input.hints['approved_scope']['allowed_paths'],
             memory.current_base_commit(active_workspace))
    staged = entry.get('self_development_snapshot')
    if staged:
        if staged != proposal['proposal_sha256']:
            raise ValueError('SELF_REPAIR_CANDIDATE_PROPOSAL_CONFLICT')
        return
    for rel, expected in proposal['source_files'].items():
        source = Path(active_workspace) / rel
        target = candidate / rel
        for root in (Path(active_workspace), candidate):
            if (policy_decision('inspect', task.task_id, str(root), paths=[rel])['decision'] != 'AUTO_APPROVE'
                    or any(root.joinpath(*Path(rel).parts[:i]).is_symlink()
                           for i in range(1, len(Path(rel).parts) + 1))):
                raise ValueError('SELF_REPAIR_SNAPSHOT_SCOPE_DENIED')
        if not source.is_file() or source.stat().st_size > 200_000 or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('SELF_REPAIR_SOURCE_DRIFT')
        if target.exists() and target.read_bytes() != source.read_bytes():
            # A pre-existing dirty candidate must not be overwritten.
            import subprocess
            state = subprocess.run(['git', '-C', str(candidate), 'status', '--porcelain', '--', rel],
                                   capture_output=True, text=True, check=True).stdout
            if state.strip():
                raise ValueError('SELF_REPAIR_DIRTY_CANDIDATE_RETAINED')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    data = registry._read()
    data[task.task_id]['self_development_snapshot'] = proposal['proposal_sha256']
    registry._write(data)
