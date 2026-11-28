"""Actual signed receipt and issuer-scoped preflight; all identities are fixtures."""
import copy
import hashlib
import json
import shutil
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from coding_worker_v2.tasks import Task, TaskInput
from coding_worker_v2.receipts import write_receipt
from coding_worker_v2.agent.verification import create_handoff, _canonical
from coding_worker_v2.agent.kmco_p05 import run_p05
from coding_worker_v2.agent.kmco_runtime import register_kmco_tools
from coding_worker_v2.agent.tools import ToolError
from coding_worker_v2.tests.test_kmco_runtime import catalog
from coding_worker_v2.tests import test_agent_verification as vf
from coding_worker_v2.tests import test_mcp_capabilities as cf


@unittest.skipUnless(shutil.which('openssl'), 'real signature verification requires openssl')
class P05Tests(unittest.TestCase):
    def setUp(self):
        from coding_worker_v2.tests.test_unified_continuation import UnifiedContinuationTest
        self.fixture = UnifiedContinuationTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.signer, self.task = self.fixture.signed_fixture()
        self.ctx = {**self.fixture.ctx, 'mission_id': self.fixture.plan.mission_id,
                    'task_id': self.task.task_id, 'owner_node': 'AV15'}
        self.plan = self.fixture.plan.to_dict()

    def test_actual_signed_receipt_gate_does_not_change_task(self):
        result = run_p05(self.plan, self.ctx)
        self.assertEqual(result['status'], 'COMPLETION_EVIDENCE_VALIDATED')
        self.assertEqual(len(result['steps_completed']), 5)
        self.assertEqual(self.task.status, 'DONE')
        self.assertFalse(result['authority_granted'])

    def test_missing_runtime_and_cross_task_cannot_self_attest(self):
        with self.assertRaisesRegex(ToolError, 'P05_RUNTIME_CONTEXT_REQUIRED'):
            run_p05(self.plan, {**self.ctx, 'account_id': ''})
        with self.assertRaisesRegex(ToolError, 'P05_TASK_SCOPE_DENIED'):
            run_p05(self.plan, {**self.ctx, 'task_id': 'other'})
        with self.assertRaisesRegex(ToolError, 'P05_RUNTIME_CONTEXT_REQUIRED'):
            run_p05(self.plan, {**self.ctx, 'mission_id': 'other'})

    def test_forged_signature_denied(self):
        data = json.loads(self.signer.proof_path.read_text())
        data['payload']['evidence_refs'] = ['forged']
        self.signer.proof_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ToolError, 'P05_COMPLETION_NOT_VERIFIED'):
            run_p05(self.plan, self.ctx)

    def test_receipt_tamper_and_missing_qc_denied(self):
        p = Path(self.ctx['receipts_dir']) / (self.task.task_id + '.json')
        data = json.loads(p.read_text()); data['evidence'] = []
        p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ToolError, 'P05_TEST_OR_QC_EVIDENCE_REQUIRED'):
            run_p05(self.plan, self.ctx)

    def test_missing_artifact_and_changed_source_denied(self):
        with self.assertRaisesRegex(ToolError, 'P05_ARTIFACT_INVALID'):
            run_p05(self.plan, {**self.ctx, 'receipts_dir': str(self.fixture.root/'absent')})
        from unittest import mock
        with mock.patch('coding_worker_v2.agent.executor.current_base_commit', return_value='0'*40):
            with self.assertRaisesRegex(ToolError, 'P05_COMPLETION_NOT_VERIFIED'):
                run_p05(self.plan, self.ctx)


class P06Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = cf.CapabilityTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.reg = self.fixture.reg
        self.ctx = self.fixture.ctx
        register_kmco_tools(self.reg, catalog())
        self.args = {'tool': 'queue_health', 'arguments': {}}
        binding = copy.deepcopy(self.ctx['tool_bindings']['queue_health'])
        binding.pop('resource_ref', None)
        binding['arguments'] = self.args
        self.ctx['tool_bindings']['kmco.p06.permission'] = binding
        self.ctx['command_auth']['permissions'].append('kmco.p06.permission')

    def test_real_mcp_grant_pass_preflight_does_not_dispatch(self):
        before = self.ctx['store'].health()
        result = self.reg.call('kmco.p06.permission', self.args, self.ctx, mcp=True)
        self.assertEqual(result['status'], 'READ_PREFLIGHT_PASS_NOT_EXECUTED')
        self.assertFalse(result['dispatched'])
        self.assertEqual(self.ctx['store'].health(), before)
        self.assertFalse(any(e.get('tool') == 'queue_health' for e in self.ctx['evidence']))

    def test_unknown_schema_argument_permission_and_revocation_denied(self):
        for args, code in (({'tool': 'unknown', 'arguments': {}}, 'P06_CAPABILITY'),
                           ({'tool': 'queue_health', 'arguments': {'spoof': True}}, 'TOOL_ARG_UNKNOWN')):
            with self.assertRaisesRegex(ToolError, code):
                self.reg.call('kmco.p06.permission', args, self.ctx)
        self.ctx['tool_bindings']['queue_health']['qualified'] = False
        with self.assertRaisesRegex(ToolError, 'P06_GRANT_DENIED'):
            self.reg.call('kmco.p06.permission', self.args, self.ctx)

    def test_missing_outer_mcp_grant_and_wrong_task_are_denied(self):
        self.ctx['tool_bindings'].pop('kmco.p06.permission')
        with self.assertRaisesRegex(ToolError, 'MCP_TOOL_SCOPE_DENIED'):
            self.reg.call('kmco.p06.permission', self.args, self.ctx, mcp=True)
        self.ctx['task_id'] = 'wrong'
        with self.assertRaisesRegex(ToolError, 'P06_GRANT_DENIED'):
            self.reg.call('kmco.p06.permission', self.args, self.ctx)

    def test_p05_registry_does_not_accept_paths_or_proof_claims(self):
        with self.assertRaisesRegex(ToolError, 'TOOL_ARG_UNKNOWN'):
            self.reg.call('kmco.p05.completion', {'receipt_path': '/etc/passwd'}, self.ctx)
        with self.assertRaisesRegex(ToolError, 'MCP_TOOL_SCOPE_DENIED'):
            self.reg.call('kmco.p05.completion', {'plan': {}}, self.ctx, mcp=True)
