#!/usr/bin/env python3
"""Bounded live local MCP qualification, then one existing-subscription Codex call."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
from datetime import datetime, timezone


def main():
    assert os.environ.get('RUNNER_NAME') == 'ubuntu-usb' and os.geteuid() != 0
    home = Path.home()
    dest = Path(os.environ['RUNNER_TEMP'])
    report = {'schema': 'KMCO_INCREMENTAL_LIVE_QUALIFICATION_V1',
              'source_sha': os.environ['SOURCE_SHA'],
              'observed_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'LOCAL_READ_TOOLS_AND_ONE_CODEX_DISPATCH',
              'production_acceptance': 'NOT_GRANTED', 'independent_qc': 'NOT_RUN',
              'calls': [], 'codex': {'attempts': 0, 'status': 'NOT_RUN'}}
    try:
        from coding_worker_v2.agent.owner_cli import load_owner_runtime
        from coding_worker_v2.agent.memory import current_base_commit
        cfg = home / '.config/knowingmind/kmcw-owner-runtime.json'
        ctx, _ = load_owner_runtime(cfg)
        prior = json.loads((dest / 'kmco-local-tools-call.json').read_text())
        assert prior['status'] == 'LOCAL_TOOLS_CALL_PASS'
        rel = prior['p01_source_ref']
        path = Path(ctx['workspace']) / rel
        quote = next(v for v in path.read_text('utf-8').splitlines() if 4 <= len(v.strip()) <= 160)
        snapshot = {'mission_id': ctx['mission_id'], 'task_id': ctx['task_id'],
                    'source_sha': current_base_commit(ctx['workspace']), 'scope': [rel],
                    'constraints': ['READ_ONLY', 'NO_PRODUCTION_ACCEPTANCE']}
        plan = {'schema': 'KM_TASK_PLAN_V1', 'mission_id': ctx['mission_id'],
                'plan_id': 'kmcp-local-read-qualification', 'owner_node': ctx['owner_node'],
                'source_command': 'Validate existing source evidence without mutation',
                'tasks': [{'task_id': 'inspect-existing-source', 'goal': 'Check existing source evidence',
                           'acceptance': ['Exact source quote verified'], 'allowed_paths': [rel]}]}
        source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        attempts = [{'attempt': i, 'mission_id': ctx['mission_id'], 'task_id': ctx['task_id'],
                     'failure_class': 'CONTROLLED_REPLAY', 'source_sha256': source_digest,
                     'progress_units': 0, 'terminal': False} for i in (1, 2, 3)]
        cases = [
            ('inspect', 'kmco.inspect', {'operation_id': 'P01-M01'}, False, 'REAL_CATALOG'),
            ('evidence', 'kmco.p01.evidence', {'file': rel, 'quote': quote}, False, 'REAL_SOURCE_FILE'),
            ('plan', 'kmco.plan', {'steps': [{'op_id': 'P01-M01', 'after': []}]}, False, 'REAL_CATALOG_PREVIEW'),
            ('resume', 'kmco.p02.resume', {'previous': snapshot, 'current': snapshot}, False, 'ACTUAL_SCOPE_SNAPSHOT'),
            ('loop', 'kmco.p03.loop_guard', {'attempts': attempts}, False, 'CONTROLLED_REPLAY_NOT_INCIDENT'),
            ('validate', 'kmco.p04.plan', {'plan': plan}, False, 'PLAN_PREVIEW_NOT_DISPATCH'),
            ('unknown-op', 'kmco.inspect', {'operation_id': 'P99-M99'}, True, 'NEGATIVE'),
            ('wrong-quote', 'kmco.p01.evidence', {'file': rel, 'quote': 'absent-quote-' + source_digest}, True, 'NEGATIVE'),
            ('resume-scope', 'kmco.p02.resume', {'previous': snapshot, 'current': {**snapshot, 'task_id': 'wrong-task'}}, True, 'NEGATIVE'),
            ('bad-budget', 'kmco.p03.loop_guard', {'attempts': attempts, 'max_attempts': 0}, True, 'NEGATIVE'),
            ('wrong-owner', 'kmco.p04.plan', {'plan': {**plan, 'owner_node': 'WRONG_OWNER'}}, True, 'NEGATIVE'),
            ('cycle', 'kmco.plan', {'steps': [{'op_id': 'P01-M01', 'after': ['P01-M01']}]}, True, 'NEGATIVE')]
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        env['PATH'] = ':'.join([str(home / '.npm-global/bin'), str(home / '.local/bin'), env.get('PATH', '')])
        py = home / 'KnowingMind/.venvs/kmcp-portable/bin/python'
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                                'clientInfo': {'name': 'kmco-live-qualification', 'version': '1'}}},
                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'}]
        requests += [{'jsonrpc': '2.0', 'id': i + 2, 'method': 'tools/call',
                      'params': {'name': tool, 'arguments': args}}
                     for i, (_, tool, args, _, _) in enumerate(cases)]
        proc = subprocess.run([str(py), '-I', '-m', 'coding_worker_v2.agent.kmco_local_mcp',
                               '--config', str(cfg)], env=env, text=True, capture_output=True,
                              input=''.join(json.dumps(v) + '\n' for v in requests), timeout=75)
        assert proc.returncode == 0, 'Local MCP subprocess failed'
        replies = {v['id']: v for line in proc.stdout.splitlines() if line.strip()
                   for v in [json.loads(line)] if 'id' in v}
        outputs = {}
        for i, (label, tool, args, expected, origin) in enumerate(cases):
            result = replies[i + 2].get('result', {})
            matched = result.get('isError') is expected
            text = next((v['text'] for v in result.get('content', []) if v.get('type') == 'text'), '{}')
            value = json.loads(text)
            outputs[label] = value
            report['calls'].append({'case': label, 'tool': tool, 'origin': origin,
                'expected_error': expected, 'observed_error': result.get('isError'),
                'passed': matched, 'reason': value.get('reason') if expected else None,
                'input_sha256': hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest(),
                'response_sha256': hashlib.sha256(text.encode()).hexdigest()})
        assert all(v['passed'] for v in report['calls']), 'Local handler case failed; inspect case evidence'
        assert outputs['loop']['stop'] is True and outputs['loop']['retry_authorized'] is False
        assert outputs['resume']['execution_done'] is False and outputs['resume']['receipt_verified'] is False
        assert outputs['validate']['admitted'] is False and outputs['validate']['dispatched'] is False
        report['local_tools'] = {'passed_cases': len(cases), 'unique_tools': 6,
                                 'synthetic_inputs_labeled': True}
        if os.environ.get('KMCO_CODEX_LIVE') != '1':
            report['status'] = 'LOCAL_TOOLS_PASS_CODEX_NOT_REQUESTED'
            (dest / 'kmco-incremental-qualification.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2))
            return 0
        # A single real Codex CLI turn. Disable unrelated MCP servers in this turn only.
        codex = shutil.which('codex', path=env['PATH'])
        assert codex, 'Existing Codex executable missing'
        config = tomllib.loads((home / '.codex/config.toml').read_text())
        argv = [codex, 'exec', '--json', '--sandbox', 'read-only',
                '-C', ctx['workspace'], '--output-last-message', str(dest / 'kmco-codex-private.txt')]
        for server in config.get('mcp_servers', {}):
            if server != 'kmcp-v5':
                argv += ['-c', 'mcp_servers.' + json.dumps(server) + '.enabled=false']
        argv += ['-c', 'model_reasoning_effort="low"']
        prompt = ('Use only the kmcp-v5 MCP server. Do not run shell, browse, edit files, or delegate. '
                  'Call kmco.inspect once with operation_id P01-M01, then call kmco.p01.evidence once '
                  'with arguments ' + json.dumps({'file': rel, 'quote': quote}) + '. '
                  'If either fails, report its exact reason and stop. Otherwise report these two tool results '
                  'in under 100 words. These reads do not prove production acceptance. Do not call any other tool.')
        argv.append(prompt)
        report['codex']['attempts'] = 1
        answer = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=180)
        events = []
        for line in answer.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.append(event)
        completed = [v.get('item', {}) for v in events if v.get('type') == 'item.completed']
        observed = []
        for item in completed:
            if item.get('type') != 'mcp_tool_call':
                continue
            result = item.get('result')
            observed.append({'server': item.get('server'), 'tool': item.get('tool'),
                'status': item.get('status'), 'isError': result.get('isError') if isinstance(result, dict) else None,
                'result_sha256': hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()})
        usage = next((v.get('usage') for v in reversed(events) if v.get('type') == 'turn.completed'), None)
        report['codex'].update(returncode=answer.returncode, calls=observed, usage=usage,
                                billed_cost='UNKNOWN_EXISTING_SUBSCRIPTION',
                                status='VERIFIED_TOOL_CALLS' if answer.returncode == 0 and
                                {v['tool'] for v in observed if v['server'] == 'kmcp-v5'
                                 and v['status'] == 'completed' and v['isError'] is False}
                                >= {'kmco.inspect', 'kmco.p01.evidence'} else 'NOT_VERIFIED')
        report['status'] = 'LOCAL_AND_CODEX_PASS' if report['codex']['status'] == 'VERIFIED_TOOL_CALLS' else 'LOCAL_PASS_CODEX_NOT_VERIFIED'
    except subprocess.TimeoutExpired:
        report['status'] = 'BLOCKED_TIMEOUT_NO_RETRY'
    except Exception as exc:
        report['status'] = 'BLOCKED'
        report['blocker'] = str(exc)[:180] if isinstance(exc, AssertionError) else type(exc).__name__
    (dest / 'kmco-incremental-qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'LOCAL_AND_CODEX_PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
