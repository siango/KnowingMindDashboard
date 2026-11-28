#!/usr/bin/env python3
"""Connect Codex to the existing local KMCP stdio and verify shared KMCW/KMCO registry."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone


def run(argv, env, **kw):
    return subprocess.run([str(a) for a in argv], env=env, capture_output=True,
                          text=True, timeout=45, **kw)


def protocol(argv, env):
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'kmcp-local-actions-acceptance', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'ping', 'params': {}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
            'name': 'kmco.inspect', 'arguments': {'operation_id': 'P01-M01'}}}]
    p = run(argv, env, input=''.join(json.dumps(v) + '\n' for v in requests))
    assert p.returncode == 0, 'Local stdio process failed'
    replies = {v['id']: v for line in p.stdout.splitlines() if line.strip()
               for v in [json.loads(line)] if 'id' in v}
    assert replies[1]['result']['protocolVersion'] == '2025-06-18'
    names = sorted(v['name'] for v in replies[2]['result']['tools'])
    assert replies[3]['result'] == {}
    assert replies[4]['result']['isError'] is True, 'Unbound MCP call must remain denied'
    return names, {'initialize': 'PASS', 'tools_list': 'PASS', 'ping': 'PASS',
                   'unbound_call': 'DENIED', 'tool_count': len(names)}


def main():
    assert os.environ.get('RUNNER_NAME') == 'ubuntu-usb' and os.geteuid() != 0
    home = Path.home()
    report = {'schema': 'KMCP_LOCAL_CONNECTION_V1', 'source_sha': os.environ['SOURCE_SHA'],
              'observed_at': datetime.now(timezone.utc).isoformat(),
              'transport': 'LOCAL_STDIO_AND_DIRECT_PYTHON',
              'independent_qc': 'NOT_RUN', 'production_acceptance': 'NOT_GRANTED'}
    out = Path(os.environ['RUNNER_TEMP']) / 'kmcp-local-connection.json'
    try:
        py = home / 'KnowingMind/.venvs/kmcp-portable/bin/python'
        cfg = home / '.config/knowingmind/kmcw-owner-runtime.json'
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        env['PATH'] = ':'.join([str(home / '.npm-global/bin'), str(home / '.local/bin'), env.get('PATH', '')])
        from coding_worker_v2.agent.owner_cli import load_owner_runtime
        ctx, registry = load_owner_runtime(cfg)
        base = [py, '-I', '-m', 'coding_worker_v2.agent.circuit_cli', '--config', cfg, 'serve']
        names, report['kmcp_stdio'] = protocol(base, env)
        worker_names, report['kmcw_local_mcp'] = protocol([home / '.local/bin/kmcw', 'mcp', '--config', cfg, 'serve'], env)
        expected = sorted(v['name'] for v in registry.command_contract()['commands'])
        assert names == worker_names == expected, 'Local clients do not expose same registry'
        assert 'kmco.p01.evidence' in names
        report['shared_registry'] = 'PASS'
        # Existing POSIX owner local Python interface, never an MCP identity grant.
        result = registry.call('kmco.inspect', {'operation_id': 'P01-M01'}, ctx, mcp=False)
        assert result['operation_id'] == 'P01-M01'
        report['kmco_direct_owner_read'] = 'PASS'
        report['kmco_direct_result_sha256'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        codex = shutil.which('codex', path=env['PATH'])
        assert codex, 'Existing Codex executable not found'
        config = home / '.codex/config.toml'
        assert config.is_file() and not config.is_symlink(), 'Codex config unavailable or unexpected symlink'
        assert config.stat().st_uid == os.getuid(), 'Codex config owner mismatch'
        previous = config.read_bytes()
        digest = hashlib.sha256(previous).hexdigest()[:12]
        backup = config.with_name('config.toml.before-local-' + digest)
        if not backup.exists():
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(previous)
        # Keep existing server name; replace its HTTPS transport with the real local command.
        p = run([codex, 'mcp', 'add', 'kmcp-v5', '--', *base], env)
        assert p.returncode == 0, 'Codex local MCP registration failed'
        get = run([codex, 'mcp', 'get', 'kmcp-v5', '--json'], env)
        assert get.returncode == 0, 'Codex MCP config readback failed'
        entry = json.loads(get.stdout)
        transport = entry['transport']
        assert transport['type'] == 'stdio' and transport['command'] == str(py), 'Codex is not configured for local stdio'
        assert transport['args'] == [str(v) for v in base[1:]], 'Codex local arguments mismatch'
        assert entry.get('enabled', True) is True
        codex_names, report['codex_configured_command'] = protocol([transport['command'], *transport['args']], env)
        assert codex_names == names
        report['codex_registration'] = 'LOCAL_STDIO_CONFIGURED_AND_COMMAND_VERIFIED'
        report['running_codex_session_reload'] = 'REQUIRED_TO_LOAD_CHANGED_SERVER_CONFIG'
        report['authority'] = 'EXISTING_PER_TOOL_GATES_PRESERVED'
        report['status'] = 'LOCAL_CONNECTION_PASS'
    except Exception as exc:
        report['status'] = 'BLOCKED'
        report['blocker'] = str(exc)[:180] if isinstance(exc, AssertionError) else type(exc).__name__
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'LOCAL_CONNECTION_PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
