"""Native database binding refuses JSON claims; no real owner credentials in tests."""
import unittest
from unittest.mock import patch
from coding_worker_v2.agent.native_database import (
    scoped_database_context, bind_owner_readonly, NativeOwnerReadAuthority, NativeReader, SQL)
from coding_worker_v2.agent.tools import ToolError


class NativeDatabaseTests(unittest.TestCase):
    def test_remote_claims_and_root_cannot_create_native_authority(self):
        for marker in (None, {}, {'uid': 1000}):
            with self.assertRaises(ToolError):
                scoped_database_context({'_native_owner_read_authority': marker, 'account_id': 'posix:1000'})
        with patch('os.getuid', return_value=0):
            marker = NativeOwnerReadAuthority()
            with self.assertRaisesRegex(ToolError, 'NATIVE_DATABASE_POSIX_OWNER_REQUIRED'):
                bind_owner_readonly({'_native_owner_read_authority': marker, 'account_id': 'posix:0'})

    def test_existing_database_authority_is_retained(self):
        ctx = {'database_bindings': {'existing': object()}}
        self.assertIs(scoped_database_context(ctx), ctx)
        with patch('os.getuid', return_value=1000):
            marker = NativeOwnerReadAuthority()
            with self.assertRaisesRegex(ToolError, 'EXISTING_AUTHORITY_PRESERVED'):
                bind_owner_readonly({**ctx, 'database_auth': {'existing': True},
                    'account_id': 'posix:1000', 'kmco_pg_service': 'kmco_runtime',
                    '_native_owner_read_authority': marker})

    def test_native_reader_refuses_caller_sql_without_connecting(self):
        for operation, sql in (('other', SQL), ('same_task_observations', 'SELECT secret')):
            with self.assertRaisesRegex(ToolError, 'NATIVE_DATABASE_OPERATION_DENIED'):
                NativeReader('kmco_runtime').read(operation, {'sql': sql}, {}, {}, 1)
