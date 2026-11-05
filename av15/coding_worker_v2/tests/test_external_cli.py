"""External CLI contracts; mocked inference is not live provider acceptance."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from coding_worker_v2.agent.providers_ext import CodexCliProvider, CopilotCliProvider
from coding_worker_v2.config import WorkerConfig
from coding_worker_v2.providers import ProviderError, redact

class TestExternalCLI(unittest.TestCase):
    def test_codex_authenticated_but_recursive_dispatch_blocked(self):
        provider = CodexCliProvider(WorkerConfig(), authorized=True)
        def command(argv):
            return subprocess.CompletedProcess(argv, 0, 'codex-cli test', '')
        with patch.object(provider, 'binary', return_value='codex'), patch('coding_worker_v2.agent.providers_ext.probe_command', side_effect=command), patch('coding_worker_v2.agent.providers_ext.supervised_by_codex', return_value=True):
            report = provider.probe()
            self.assertTrue(report['authenticated'])
            self.assertFalse(report['available'])
            self.assertIn('RECURSION_BLOCKED', report['reason'])
            with self.assertRaises(ProviderError):
                provider.generate([])

    def test_copilot_broken_platform_is_unavailable(self):
        provider = CopilotCliProvider(WorkerConfig(), authorized=True)
        with patch.object(provider, 'binary', return_value='copilot'), patch('coding_worker_v2.agent.providers_ext.probe_command', return_value=subprocess.CompletedProcess([], 1, '', 'no platform package found')):
            self.assertEqual(provider.probe()['reason'], 'COPILOT_PLATFORM_PACKAGE_MISSING')

    def test_cli_permissions_and_result_capture(self):
        provider = CopilotCliProvider(WorkerConfig(), authorized=True)
        with patch.object(provider, 'is_available', return_value=(True, 'OK')), patch.object(provider, 'binary', return_value='copilot'), patch('coding_worker_v2.agent.providers_ext.run_bounded', return_value=(0, '{"edits":[{"path":"a.py","content":"x=1"}]}', 12)) as execute:
            result = provider.generate([{'role': 'user', 'content': 'bounded'}])
        argv = execute.call_args.args[0]
        self.assertIn('--available-tools=view,glob,grep', argv)
        self.assertIn('--deny-tool=shell,write', argv)
        self.assertNotIn('--allow-all', argv)
        self.assertEqual(result.execution['exit_status'], 0)
        self.assertEqual(result.execution['elapsed_ms'], 12)
        self.assertEqual(execute.call_args.kwargs['env']['KMW2_PROVIDER_DEPTH'], '1')
        codex = CodexCliProvider(WorkerConfig())
        with tempfile.TemporaryDirectory() as directory:
            argv, _ = codex.argv(Path(directory))
        self.assertIn('read-only', argv)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)

    def test_cli_nonzero_exit_never_reports_success(self):
        provider = CopilotCliProvider(WorkerConfig(), authorized=True)
        with patch.object(provider, 'is_available', return_value=(True, 'OK')), patch.object(provider, 'binary', return_value='copilot'), patch('coding_worker_v2.agent.providers_ext.run_bounded', return_value=(7, 'not success', 12)):
            with self.assertRaisesRegex(ProviderError, 'exit_status=7'):
                provider.generate([])

    def test_receipt_redaction_of_external_credentials(self):
        with patch.dict(os.environ, {'GH_TOKEN':'sensitive-opaque-value'}):
            self.assertNotIn('sensitive-opaque-value',redact('value=sensitive-opaque-value'))
        self.assertNotIn('ghp_123456789abcdefgh',redact('ghp_123456789abcdefgh'))
