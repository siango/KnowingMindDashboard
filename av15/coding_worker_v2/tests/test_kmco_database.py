"""D01/D02 original DB router/ACL/audit integration. Fixtures, no live acceptance."""
import unittest
from coding_worker_v2.agent.kmco_database import run_d01, run_d02
from coding_worker_v2.agent.kmco_runtime import register_kmco_tools
from coding_worker_v2.agent.tools import ToolError
from coding_worker_v2.tests import test_database_tools as fixtures
from coding_worker_v2.tests.test_kmco_runtime import catalog


class DomainDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DatabaseToolsTest(); self.fixture.setUp()
        self.ctx, self.reg, self.binding = self.fixture.ctx, self.fixture.reg, self.fixture.binding
        self.binding.update(schema_revision='fixture-schema-1', tenant_id='fixture-tenant', freshness_ttl_s=60)
        self.ctx['tenant_id'] = 'fixture-tenant'
        register_kmco_tools(self.reg, catalog())
        self.args = {'system': 'postgres', 'target_id': 'staging', 'operation': 'get'}

    def test_exact_authority_resolution_never_probes_or_leaks_adapter(self):
        out = self.reg.call('kmco.d01.authority', self.args, self.ctx)
        self.assertEqual(len(out['steps_completed']), 6)
        self.assertEqual(out['handler'], 'db.postgres.select')
        self.assertFalse(out['authority_granted'])
        self.fixture.adapter.read.assert_not_called()
        self.assertNotIn('adapter', str(out))

    def test_read_executes_original_registry_and_durable_audit(self):
        out = self.reg.call('kmco.d02.read', {**self.args, 'limit': 3}, self.ctx)
        self.assertEqual(out['readback']['data']['rows'], [{'value':'retained'}])
        self.assertEqual([e['phase'] for e in self.fixture.events], ['attempt', 'result'])
        self.assertEqual(out['independent_qc'], 'NOT_RUN')
        self.assertEqual(self.fixture.adapter.read.call_args.args[-1], 3)
        self.assertEqual(len(out['steps_completed']), 6)

    def test_no_sql_from_caller_and_exact_parameter_schema(self):
        for change in ({'params': {'sql': 'DELETE FROM data'}}, {'limit': True},
                       {'limit': 101}, {'cursor': 'invented'}):
            with self.subTest(change=change), self.assertRaises(ToolError):
                self.reg.call('kmco.d02.read', {**self.args, **change}, self.ctx)
        self.fixture.adapter.read.assert_not_called()

    def test_tenant_schema_source_expiry_and_owner_mismatch_deny_before_read(self):
        cases = [(self.ctx, 'tenant_id', 'other'), (self.binding, 'schema_revision', ''),
                 (self.binding, 'system', 'd1'), (self.ctx['database_auth'], 'expires_at', 99),
                 (self.ctx['database_auth'], 'source_sha', 'other')]
        for target, key, value in cases:
            old = target[key]; target[key] = value
            with self.subTest(key=key), self.assertRaises(ToolError):
                self.reg.call('kmco.d02.read', self.args, self.ctx)
            target[key] = old
        self.fixture.adapter.read.assert_not_called()

    def test_missing_binding_and_outer_mcp_grant_fail_closed(self):
        with self.assertRaises(ToolError):
            self.reg.call('kmco.d02.read', self.args, self.ctx, mcp=True)
        self.ctx.pop('database_bindings')
        with self.assertRaisesRegex(ToolError, 'D01_NATIVE_OR_DATABASE_AUTHORITY_REQUIRED'):
            self.reg.call('kmco.d01.authority', self.args, self.ctx)

    def test_audit_failure_never_returns_rows(self):
        self.binding['audit'] = lambda e: None
        with self.assertRaisesRegex(ToolError, 'DB_DURABLE_AUDIT_REQUIRED'):
            self.reg.call('kmco.d02.read', self.args, self.ctx)
        self.fixture.adapter.read.assert_not_called()
