#!/usr/bin/env python3
"""Repair only the existing owner CLI bootstrap; preserve audit and execution gates."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
from datetime import datetime, timezone


def probe(argv, cwd=None, env=None):
    p = subprocess.run([str(v) for v in argv], cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=45)
    return p


def main():
    assert os.environ.get('RUNNER_NAME') == 'ubuntu-usb'
    assert os.geteuid() != 0
    home = Path.home()
    out = Path(os.environ['RUNNER_TEMP']) / 'kmcw-bootstrap-repair.json'
    report = {'schema': 'KMCW_BOOTSTRAP_REPAIR_V1',
              'source_sha': os.environ['SOURCE_SHA'],
              'observed_at': datetime.now(timezone.utc).isoformat(),
              'production_acceptance': 'NOT_GRANTED',
              'independent_qc': 'NOT_RUN', 'steps': {}}
    steps = report['steps']
    try:
        py = home / 'KnowingMind/.venvs/kmcp-portable/bin/python'
        cfg = home / '.config/knowingmind/kmcw-owner-runtime.json'
        assert py.is_file() and cfg.is_file(), 'Existing runtime/config missing'
        assert cfg.stat().st_uid == os.getuid() and not cfg.stat().st_mode & 0o077, 'Owner config must be private'
        config = json.loads(cfg.read_text())
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        env['PATH'] = str(home / '.npm-global/bin') + ':' + str(home / '.local/bin') + ':' + env.get('PATH', '')
        p = probe([py, '-I', '-c', 'import coding_worker_v2.agent.ssh_cli as m; print(m.__file__)'], cwd=os.environ['RUNNER_TEMP'], env=env)
        assert p.returncode == 0, 'Installed isolated ssh_cli import failed'
        assert str(py.parent.parent) in p.stdout, 'Import is outside installed runtime'
        steps['installed_import'] = 'PASS'
        root = home / 'KnowingMind/workspaces/km-mission-auditor-source'
        from coding_worker_v2.agent.auditor import AUDITOR_COMMIT, AUDITOR_BLOB, MANIFEST_BLOB, _gate_passed
        head = probe(['git', '-C', root, 'rev-parse', 'HEAD'], env=env)
        assert head.returncode == 0 and head.stdout.strip() == AUDITOR_COMMIT, 'Existing auditor checkout SHA mismatch or missing'
        for rel, expected in [('scripts/control/km_mission_auditor.py', AUDITOR_BLOB), ('docs/control/KM_UNIFIED_MISSION_ACCEPTANCE_V1.json', MANIFEST_BLOB)]:
            data = (root / rel).read_bytes()
            assert hashlib.sha1(('blob %d\0' % len(data)).encode() + data).hexdigest() == expected, 'Auditor file digest mismatch'
        env['KMW2_MISSION_AUDITOR_ROOT'] = str(root)
        steps['auditor_source'] = 'PINNED_SOURCE_PASS'
        # Use only configured snapshot locations. Do not invent authority observations.
        snapshot = env.get('KMW2_MISSION_AUDITOR_SNAPSHOT') or config.get('mission_auditor_snapshot') or config.get('auditor_snapshot')
        cmd = [py, '-I', root / 'scripts/control/km_mission_auditor.py', '--out', Path(os.environ['RUNNER_TEMP']) / 'kmcw-audit-private.json']
        if snapshot:
            candidate = Path(snapshot).expanduser()
            if not candidate.is_absolute():
                candidate = cfg.parent / candidate
            assert candidate.is_file() and candidate.stat().st_uid == os.getuid(), 'Configured authority snapshot unavailable'
            cmd += ['--snapshot', candidate]
            env['KMW2_MISSION_AUDITOR_SNAPSHOT'] = str(candidate)
        audit = probe(cmd, cwd=root, env=env)
        assert audit.returncode == 0, 'Canonical auditor execution failed'
        audit_path = Path(os.environ['RUNNER_TEMP']) / 'kmcw-audit-private.json'
        audit_path.chmod(0o600)
        audit_report = json.loads(audit_path.read_text())
        steps['authority_audit'] = {
            'gate_passed': _gate_passed(audit_report),
            'observed_requirements': audit_report.get('observed_requirements'),
            'requirements_missing': audit_report.get('requirements_missing'),
            'findings': [v.get('code') for v in audit_report.get('global_findings', [])]}
        # Repair only a recognized, owner-owned launcher after validating imports and pinned auditor.
        launcher = home / '.local/bin/kmcw'
        assert launcher.is_file() and not launcher.is_symlink(), 'Unexpected launcher type; preserve it'
        assert launcher.stat().st_uid == os.getuid(), 'Launcher owner mismatch'
        old = launcher.read_text()
        assert 'coding_worker_v2.agent.ssh_cli' in old and old.startswith('#!'), 'Unrecognized launcher; preserve it'
        first, body = old.split('\n', 1)
        assert str(py) in first, 'Launcher uses a different interpreter; preserve it'
        injection = ('import os as _km_boot_os\n'
                     + '_km_boot_os.environ.setdefault("KMW2_MISSION_AUDITOR_ROOT", ' + repr(str(root)) + ')\n')
        if '_km_boot_os' in body:
            new = '#!' + str(py) + ' -I\n' + body
        else:
            assert 'from __future__' not in body, 'Unexpected launcher layout'
            new = '#!' + str(py) + ' -I\n' + injection + body
        backup = launcher.with_name('kmcw.before-bootstrap-' + hashlib.sha256(old.encode()).hexdigest()[:12])
        if new != old:
            if not backup.exists():
                with backup.open('x') as f:
                    f.write(old)
                backup.chmod(stat.S_IMODE(launcher.stat().st_mode))
            temp = launcher.with_name('.kmcw.bootstrap-' + str(os.getpid()))
            with temp.open('x') as f:
                f.write(new)
            temp.chmod(stat.S_IMODE(launcher.stat().st_mode))
            assert launcher.read_text() == old, 'Launcher changed concurrently; preserve it'
            temp.replace(launcher)
        smoke_env = dict(env, PYTHONPATH='/nonexistent-kmcw-pollution')
        smoke = probe([launcher, '--help'], cwd=os.environ['RUNNER_TEMP'], env=smoke_env)
        if smoke.returncode:
            if new != old and launcher.read_text() == new:
                launcher.write_text(old)
            raise RuntimeError('Launcher smoke failed; rolled back')
        steps['launcher'] = {'status': 'PASS', 'isolated': True, 'backup_created': new != old,
                             'sha256': hashlib.sha256(launcher.read_bytes()).hexdigest()}
        status = probe([launcher, 'worker', '--config', cfg, '--', 'status'], cwd=os.environ['RUNNER_TEMP'], env=env)
        steps['worker_status'] = {'returncode': status.returncode}
        # Do not expose task contents, tokens, environment values or private audit matrix in logs.
        report['status'] = 'BOOTSTRAP_REPAIRED' if status.returncode == 0 else 'BOOTSTRAP_PASS_WORKER_STATUS_BLOCKED'
        report['next_action'] = 'EXISTING_PLAN_EXECUTION_REQUIRES_VALID_AUTHORITY' if not _gate_passed(audit_report) else 'EXISTING_PLAN_READY_FOR_PROVIDER_CHECK'
    except Exception as exc:
        report['status'] = 'BLOCKED'
        report['blocker'] = str(exc)[:180] if isinstance(exc, (AssertionError, RuntimeError)) else type(exc).__name__
    finally:
        out.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    return 1 if report['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
