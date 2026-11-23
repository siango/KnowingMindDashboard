import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from coding_worker_v2.agent.kmco_local_mcp import LocalOwnerRegistry
from coding_worker_v2.agent.tools import ToolError


class Registry:
    def __init__(self):
        self.calls = []

    def command_contract(self):
        return {'commands': [{'name': 'kmco.inspect', 'permission': 'read', 'required_permissions': []}]}

    def validate_arguments(self, name, args):
        if set(args) != {'operation_id'}:
            raise ToolError('TOOL_ARGS_INVALID')

    def call(self, name, args, ctx, *, mcp=False):
        self.calls.append((name, args, ctx, mcp))
        if name != 'kmco.inspect' or not ctx.get('tool_bindings'):
            raise ToolError('MCP_TOOL_SCOPE_DENIED')
        from coding_worker_v2.agent.capabilities import authorized
        if not authorized(self.command_contract()['commands'][0], ctx, args):
            raise ToolError('MCP_TOOL_SCOPE_DENIED')
        return {'operation_id': args['operation_id']}


class LocalOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = root / 'owner.json'
        self.config.write_text('{}')
        self.ctx = {'account_id': 'posix:1002', 'mission_id': 'mission', 'task_id': 'task',
                    'workspace': str(root), 'owner_node': 'ubuntu-usb',
                    'store': SimpleNamespace(base=root / 'queue')}
        self.reg = Registry()
        self.proxy = LocalOwnerRegistry(self.config, self.ctx, self.reg)
        self.addCleanup(patch.stopall)
        self.owner = patch('coding_worker_v2.agent.kmco_local_mcp.load_owner_runtime',
                           return_value=(dict(self.ctx), self.reg)).start()
        patch('coding_worker_v2.agent.kmco_local_mcp.os.getuid', return_value=1002).start()
        patch('coding_worker_v2.agent.kmco_local_mcp.current_base_commit', return_value='a' * 40).start()
        patch('coding_worker_v2.agent.memory.current_base_commit', return_value='a' * 40).start()

    def test_read_uses_original_mcp_authorization_and_exact_arguments(self):
        result = self.proxy.call('kmco.inspect', {'operation_id': 'P01-M01'}, dict(self.ctx), mcp=True)
        self.assertEqual(result['operation_id'], 'P01-M01')
        self.assertTrue(self.reg.calls[-1][3])
        scoped = self.reg.calls[-1][2]
        self.assertTrue(scoped['read_only'])
        self.assertEqual(scoped['tool_bindings']['kmco.inspect']['arguments'], {'operation_id': 'P01-M01'})
        self.assertNotIn('command_auth', self.ctx)

    def test_write_keeps_original_denial(self):
        with self.assertRaisesRegex(ToolError, 'MCP_TOOL_SCOPE_DENIED'):
            self.proxy.call('kmcp.circuit.submit', {'manifest': {}}, dict(self.ctx), mcp=True)
        self.owner.assert_not_called()

    def test_config_scope_change_denied_before_handler(self):
        self.owner.return_value = ({**self.ctx, 'mission_id': 'other'}, self.reg)
        with self.assertRaisesRegex(ToolError, 'LOCAL_OWNER_SCOPE_CHANGED'):
            self.proxy.call('kmco.inspect', {'operation_id': 'P01-M01'}, dict(self.ctx), mcp=True)
        self.assertEqual(self.reg.calls, [])

    def test_revoked_owner_denied(self):
        self.owner.side_effect = ToolError('SSH_NATIVE_OWNER_IDENTITY_REQUIRED')
        with self.assertRaisesRegex(ToolError, 'SSH_NATIVE_OWNER_IDENTITY_REQUIRED'):
            self.proxy.call('kmco.inspect', {'operation_id': 'P01-M01'}, dict(self.ctx), mcp=True)
        self.assertEqual(self.reg.calls, [])

    def test_malformed_arguments_do_not_reach_handler(self):
        with self.assertRaisesRegex(ToolError, 'TOOL_ARGS_INVALID'):
            self.proxy.call('kmco.inspect', {'operation_id': 'P01-M01', 'authenticated': True}, dict(self.ctx), mcp=True)
        self.assertEqual(self.reg.calls, [])

    def test_root_is_not_local_owner(self):
        patch('coding_worker_v2.agent.kmco_local_mcp.os.getuid', return_value=0).start()
        with self.assertRaisesRegex(ToolError, 'SSH_NATIVE_OWNER_IDENTITY_REQUIRED'):
            self.proxy.call('kmco.inspect', {'operation_id': 'P01-M01'}, dict(self.ctx), mcp=True)


if __name__ == '__main__':
    unittest.main()
