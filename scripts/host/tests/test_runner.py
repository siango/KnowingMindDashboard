"""Execute the actual installer with local command fixtures; never register a real runner."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'install-runner.sh'


class RunnerInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.bin = self.root / 'bin'
        self.home.mkdir()
        self.bin.mkdir()
        self.runner = self.home / 'runner'
        self.log = self.root / 'calls'
        self.env = dict(os.environ, HOME=str(self.home), RUNNER_DIR=str(self.runner),
                        REPO='owner/project', RUNNER_NAME='fixture-node',
                        TEST_LOG=str(self.log), TEST_ROOT=str(self.root),
                        PATH=str(self.bin)+':'+os.environ['PATH'])
        self.command('systemctl', '[[ $1 != is-active ]] || exit "${FIXTURE_ACTIVE:-1}"')
        self.command('findmnt', 'echo "${FIXTURE_FS:-ext4}"')
        self.command('sudo', 'echo "sudo:$*" >> "$TEST_LOG"; exec "$@"')
        self.command('gh', '''
echo "gh:$*" >> "$TEST_LOG"
if [[ $1 == auth ]]; then exit 0; fi
case "$*" in
  *registration-token*) echo fixture-registration-secret;;
  *releases/latest*) cat "$TEST_ROOT/release.json";;
  *actions/runners*) printf '%s\\n' "${FIXTURE_DUPLICATE:-}";;
  *) exit 9;;
esac''')
        self.command('curl', 'cp "$TEST_ROOT/runner.tar.gz" "${@: -1}"')
        package = self.root / 'package'
        (package / 'bin').mkdir(parents=True)
        self.write_executable(package / 'bin/installdependencies.sh', 'echo dependencies >> "$TEST_LOG"')
        self.write_executable(package / 'config.sh', '''
[[ ${ACTIONS_RUNNER_INPUT_TOKEN:-} == fixture-registration-secret ]] || exit 7
echo config >> "$TEST_LOG"
printf '{"gitHubUrl":"https://github.com/owner/project"}' > .runner''')
        self.write_executable(package / 'svc.sh', '''
echo "svc:$*" >> "$TEST_LOG"
[[ $1 != install ]] || echo actions.runner.fixture.service > .service''')
        archive = self.root / 'runner.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for p in package.iterdir():
                tar.add(p, arcname=p.name)
        self.release = {'assets': [{'name': 'actions-runner-linux-x64-9.9.9.tar.gz',
            'browser_download_url': 'https://github.com/actions/runner/releases/download/v9.9.9/fixture.tar.gz',
            'digest': 'sha256:'+hashlib.sha256(archive.read_bytes()).hexdigest()}]}
        (self.root / 'release.json').write_text(json.dumps(self.release))
        self.package = package

    def tearDown(self):
        self.tmp.cleanup()

    def write_executable(self, path, body):
        path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'+body+'\n')
        path.chmod(0o755)

    def command(self, name, body):
        self.write_executable(self.bin / name, body)

    def run_script(self, **env):
        # Tests are run as a normal account, matching real installation.
        self.assertNotEqual(os.geteuid(), 0, 'Run fixture tests as an ordinary user')
        result = subprocess.run(['bash', str(SCRIPT)], env=dict(self.env, **env),
                                text=True, capture_output=True)
        self.assertNotIn('fixture-registration-secret', result.stdout+result.stderr)
        return result

    def calls(self):
        return self.log.read_text() if self.log.exists() else ''

    def configured(self, repo='owner/project', service=True):
        shutil.copytree(self.package, self.runner)
        (self.runner / '.runner').write_text(json.dumps({'gitHubUrl': 'https://github.com/'+repo}))
        if service:
            (self.runner / '.service').write_text('actions.runner.fixture.service')

    def test_fresh_install_and_repeat_without_registration(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('svc:install', self.calls())
        self.assertIn('svc:start', self.calls())
        self.assertEqual(self.calls().count('registration-token'), 1)
        result = self.run_script(FIXTURE_ACTIVE='0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls().count('registration-token'), 1)
        self.assertEqual(self.calls().count('svc:install'), 1)
        self.assertEqual(self.calls().count('svc:start'), 1)

    def test_reuses_running_service(self):
        self.configured()
        self.assertEqual(self.run_script(FIXTURE_ACTIVE='0').returncode, 0)
        self.assertNotIn('registration-token', self.calls())
        self.assertNotIn('svc:start', self.calls())

    def test_existing_config_missing_service_installs_service_only(self):
        self.configured(service=False)
        self.assertEqual(self.run_script().returncode, 0)
        self.assertIn('svc:install', self.calls())
        self.assertNotIn('registration-token', self.calls())

    def test_wrong_repository_fails_without_changes(self):
        self.configured(repo='other/project')
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertNotIn('sudo:', self.calls())

    def test_duplicate_remote_name_fails_without_replacement(self):
        self.assertNotEqual(self.run_script(FIXTURE_DUPLICATE='fixture-node').returncode, 0)
        self.assertNotIn('registration-token', self.calls())
        self.assertNotIn('sudo:', self.calls())

    def test_ephemeral_filesystem_fails(self):
        self.assertNotEqual(self.run_script(FIXTURE_FS='overlay').returncode, 0)
        self.assertNotIn('sudo:', self.calls())

    def test_checksum_mismatch_fails_before_extraction(self):
        self.release['assets'][0]['digest'] = 'sha256:'+'0'*64
        (self.root / 'release.json').write_text(json.dumps(self.release))
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse((self.runner / 'config.sh').exists())
        self.assertNotIn('registration-token', self.calls())

    def test_invalid_repo_fails(self):
        self.assertNotEqual(self.run_script(REPO='../bad').returncode, 0)
        self.assertNotIn('sudo:', self.calls())


if __name__ == '__main__':
    unittest.main()
