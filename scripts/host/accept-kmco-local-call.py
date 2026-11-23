#!/usr/bin/env python3
"""Exercise installed KMCO handlers over the local MCP wire, then configure Codex."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone


def run(argv, env, **kwargs):
    return subprocess.run([str(v) for v in argv], env=env, capture_output=True,
                          text=True, timeout=50, **kwargs)


def main():
    assert os.environ.get('RUNNER_NAME') == 'ubuntu-usb' and os.geteuid() != 0
    report = {'schema': 'KMCO_LOCAL_TOOLS_CALL_V1', 'source_sha': os.environ['SOURCE_SHA'],
              'observed_at': datetime.now(timezone.utc).isoformat(),
              'transport': 'EXISTING_LOCAL_MCP_STDIO', 'independent_qc': 'NOT_RUN',
              'production_acceptance': 'NOT_GRANTED'}
    try:
        home = Path.home()
        py = home / 'KnowingMind/.venvs/kmcp-portable/bin/python'
        cfg = home / '.config/knowingmind/kmcw-owner-runtime.json'
        from coding_worker_v2.agent.owner_cli import load_owner_runtime
        ctx, registry = load_owner_runtime(cfg)
        workspace = Path(ctx['workspace'])
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        env['PATH'] = ':'.join([str(home / '.npm-global/bin'), str(home / '.local/bin'), env.get('PATH', '')])
        tracked = run(['git', '-C', workspace, 'ls-files', '-z'], env)
        assert tracked.returncode == 0
        candidates = sorted(tracked.stdout.split('\0'), key=lambda v: ('readme' not in v.lower(), len(v)))
        chosen = None
        for rel in candidates:
            if not rel or not rel.lower().endswith(('.md', '.py')):
                continue
            path = workspace / rel
            if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 200000:
                continue
            try:
                lines = path.read_text('utf-8').splitlines()
            except UnicodeError:
                continue
            quote = next((v for v in lines if 4 <= len(v.strip()) <= 160), None)
            if quote:
                chosen = (rel, quote)
                break
        assert chosen, 'No bounded tracked source file for evidence read'
        calls = [
            ('kmco.inspect', {'operation_id': 'P01-M01'}),
            ('kmco.p01.evidence', {'file': chosen[0], 'quote': chosen[1]}),
            ('kmcp.circuit.submit', {'manifest': {}}),
            ('kmco.p01.evidence', {'file': '../outside-workspace', 'quote': 'probe'})]
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'kmco-local-call-acceptance', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'}]
        requests += [{'jsonrpc': '2.0', 'id': i + 2, 'method': 'tools/call',
                      'params': {'name': name, 'arguments': args}}
                     for i, (name, args) in enumerate(calls)]
        base = [py, '-I', '-m', 'coding_worker_v2.agent.kmco_local_mcp', '--config', cfg]
        process = run(base, env, input=''.join(json.dumps(v) + '\n' for v in requests))
        assert process.returncode == 0, 'Installed local MCP process failed'
        responses = {v['id']: v for line in process.stdout.splitlines() if line.strip()
                     for v in [json.loads(line)] if 'id' in v}
        for i in (2, 3):
            assert responses[i]['result']['isError'] is False, 'Local KMCO read call failed'
        for i in (4, 5):
            assert responses[i]['result']['isError'] is True, 'Write or path escape was not denied'
        report['calls'] = [
            {'tool': name, 'isError': responses[i + 2]['result']['isError'],
             'response_sha256': hashlib.sha256(json.dumps(responses[i + 2], sort_keys=True).encode()).hexdigest()}
            for i, (name, _) in enumerate(calls)]
        report['p01_source_ref'] = chosen[0]
        report['p01_input_sha256'] = hashlib.sha256(json.dumps(calls[1][1], sort_keys=True).encode()).hexdigest()
        codex = shutil.which('codex', path=env['PATH'])
        assert codex, 'Codex executable unavailable'
        config = home / '.codex/config.toml'
        assert config.is_file() and not config.is_symlink() and config.stat().st_uid == os.getuid()
        raw = config.read_bytes()
        backup = config.with_name('config.toml.before-owner-read-' + hashlib.sha256(raw).hexdigest()[:12])
        if not backup.exists():
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(raw)
        updated = run([codex, 'mcp', 'add', 'kmcp-v5', '--', *base], env)
        assert updated.returncode == 0, 'Codex registration failed'
        got = run([codex, 'mcp', 'get', 'kmcp-v5', '--json'], env)
        assert got.returncode == 0
        transport = json.loads(got.stdout)['transport']
        assert transport['type'] == 'stdio' and transport['command'] == str(py)
        assert transport['args'] == [str(v) for v in base[1:]]
        report['codex'] = 'CONFIGURED_FOR_VERIFIED_LOCAL_OWNER_READS'
        report['running_session'] = 'RELOAD_REQUIRED'
        report['status'] = 'LOCAL_TOOLS_CALL_PASS'
    except Exception as exc:
        report['status'] = 'BLOCKED'
        report['blocker'] = str(exc)[:160] if isinstance(exc, AssertionError) else type(exc).__name__
    output = Path(os.environ['RUNNER_TEMP']) / 'kmco-local-tools-call.json'
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'LOCAL_TOOLS_CALL_PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
