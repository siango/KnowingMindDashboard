"""External CLI contracts; mocked inference is not live provider acceptance."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from coding_worker_v2.agent.providers_ext import CodexCliProvider, CopilotCliProvider, parse_copilot_usage
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
        self.assertIn('--usage-output-file', argv)
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


USAGE = {"totalPremiumRequestCost": 1, "totalNanoAiu": 5928260000, "aiCreditsStatus": "complete",
         "modelMetrics": {"model-a": {"usage": {"inputTokens": 50270, "outputTokens": 1038,
                                                "cacheReadTokens": 31986, "cacheWriteTokens": 18272}}}}


class TestCopilotUsage(unittest.TestCase):
    def write(self, directory, data):
        path = Path(directory) / 'usage.json'
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_parse_reports_native_units_without_usd(self):
        with tempfile.TemporaryDirectory() as d:
            usage = parse_copilot_usage(self.write(d, USAGE))
        self.assertEqual(usage['usage_source'], 'COPILOT_CLI_USAGE_OUTPUT_FILE')
        self.assertEqual((usage['tokens_in'], usage['tokens_out']), (50270, 1038))
        self.assertEqual((usage['premium_requests'], usage['nano_aiu']), (1, 5928260000))
        self.assertIsNone(usage['billed_cost_usd'])
        self.assertIsNone(usage['provider_request_id'])

    def test_parse_fails_closed(self):
        bad_counts = json.loads(json.dumps(USAGE))
        bad_counts['modelMetrics']['model-a']['usage']['inputTokens'] = True
        incomplete = dict(USAGE, aiCreditsStatus='pending')
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(parse_copilot_usage(Path(d) / 'absent.json')['usage_source'],
                             'COPILOT_USAGE_UNAVAILABLE')
            for data in ('not json', {'modelMetrics': {}}, bad_counts, [1]):
                result = parse_copilot_usage(self.write(d, data))
                self.assertEqual(result['usage_source'], 'COPILOT_USAGE_UNAVAILABLE', data)
                self.assertIsNone(result['tokens_in'])
            partial = parse_copilot_usage(self.write(d, incomplete))
            self.assertEqual(partial['tokens_in'], 50270)
            self.assertIsNone(partial['premium_requests'])
            self.assertIsNone(partial['nano_aiu'])

    def test_generation_and_failed_dispatch_carry_usage(self):
        provider = CopilotCliProvider(WorkerConfig(), authorized=True)
        def run(argv, **kwargs):
            Path(argv[argv.index('--usage-output-file') + 1]).write_text(json.dumps(USAGE))
            return run.rc, '{"edits":[]}', 5
        with patch.object(provider, 'is_available', return_value=(True, 'OK')), \
                patch.object(provider, 'binary', return_value='copilot'), \
                patch('coding_worker_v2.agent.providers_ext.run_bounded', side_effect=run):
            run.rc = 0
            generation = provider.generate([{'role': 'user', 'content': 'x'}])
            run.rc = 3
            with self.assertRaises(ProviderError) as failed:
                provider.generate([])
        self.assertEqual((generation.tokens_in, generation.tokens_out), (50270, 1038))
        self.assertEqual(generation.execution['usage_source'], 'COPILOT_CLI_USAGE_OUTPUT_FILE')
        self.assertEqual(failed.exception.resources['usage']['tokens_in'], 50270)

    def test_codex_usage_remains_explicitly_uncaptured(self):
        provider = CodexCliProvider(WorkerConfig())
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(provider.collect_usage(Path(d))['usage_source'], 'NOT_CAPTURED_BY_ADAPTER')
