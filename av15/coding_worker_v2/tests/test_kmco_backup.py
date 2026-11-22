"""D12 actual git bundle/checksums/restore with fixture authority only."""
import unittest
from unittest.mock import patch
from coding_worker_v2.tests import test_runtime_backup as fixtures
from coding_worker_v2.agent.kmco_backup import run_d12,require_native_owner
from coding_worker_v2.agent.tools import ToolError
from coding_worker_v2.queue_store import QueueStore


class D12Tests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.BackupTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ctx={'workspace':str(self.fixture.source),
                  'store':QueueStore(self.fixture.runtime/'queue')}
        self.args={'output_dir':str(self.fixture.output),'restore_id':'fixture'}

    def test_real_archive_and_isolated_file_restore(self):
        with patch('coding_worker_v2.agent.kmco_backup.require_native_owner'):
            result=run_d12(self.args,self.ctx)
        self.assertEqual(len(result['steps_completed']),6)
        self.assertEqual(result['status'],'FILE_BACKUP_RESTORE_READBACK_VERIFIED')
        self.assertEqual((self.fixture.output/'restore-fixture/runtime/receipts/task.json').read_text(),'{"state":"DONE"}')
        self.assertFalse(result['production_database_restored'])
        self.assertEqual(result['independent_qc'],'NOT_RUN')

    def test_json_owner_claims_and_overwrite_are_denied(self):
        with self.assertRaisesRegex(ToolError,'NATIVE_OWNER_AUTHORITY_REQUIRED'):
            require_native_owner({'_native_owner_read_authority':{'uid':1000},'account_id':'posix:1000'})
        (self.fixture.output/'restore-fixture').mkdir()
        with patch('coding_worker_v2.agent.kmco_backup.require_native_owner'):
            with self.assertRaisesRegex(ToolError,'NEW_ISOLATED_RESTORE_REQUIRED'):
                run_d12(self.args,self.ctx)
        self.assertEqual(len(list(self.fixture.output.glob('*.tar.gz'))),0)
