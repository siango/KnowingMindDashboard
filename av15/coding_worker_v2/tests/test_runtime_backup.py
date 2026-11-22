import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from coding_worker_v2.agent.runtime_backup import create_backup, verify_backup, restore_backup
from coding_worker_v2.agent.tools import ToolError


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'source';self.source.mkdir()
        self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.output=self.root/'backups';self.output.mkdir(mode=0o700)
        def git(*args):
            return subprocess.run(['git',*args],cwd=self.source,check=True,capture_output=True)
        self.git=git;git('init');git('config','user.email','fixture@example.invalid')
        git('config','user.name','Fixture')
        (self.source/'README').write_text('source')
        git('add','README');git('commit','-m','fixture')
        (self.runtime/'receipts').mkdir()
        (self.runtime/'receipts/task.json').write_text('{"state":"DONE"}')

    def test_real_git_bundle_runtime_restore_and_permissions(self):
        result=create_backup(self.source,self.runtime,self.output)
        self.assertEqual(result['integrity'],'PASS')
        archive=Path(result['archive'])
        self.assertEqual(archive.stat().st_mode & 0o777,0o600)
        target=self.root/'restored'
        restored=restore_backup(archive,target)
        self.assertFalse(restored['database_restored'])
        self.assertEqual((target/'runtime/receipts/task.json').read_text(),'{"state":"DONE"}')
        subprocess.run(['git','bundle','verify',str(target/'recovery/source.bundle')],
                       cwd=self.source,check=True,capture_output=True)

    def test_native_credentials_and_symlinks_excluded(self):
        (self.runtime/'.env').write_text('fixture-secret')
        (self.runtime/'.ssh').mkdir();(self.runtime/'.ssh/id_ed25519').write_text('private')
        (self.runtime/'escape').symlink_to(self.source/'README')
        result=create_backup(self.source,self.runtime,self.output)
        manifest=verify_backup(result['archive'])
        names=list(manifest['files'])
        self.assertFalse(any('.env' in x or '.ssh' in x or 'escape' in x for x in names))

    def test_tracked_work_is_preserved_without_reset(self):
        (self.source/'README').write_text('modified')
        result=create_backup(self.source,self.runtime,self.output)
        manifest=verify_backup(result['archive'])
        self.assertTrue(manifest['tracked_changes'])
        self.assertIn('recovery/tracked-changes.patch',manifest['files'])
        self.assertEqual((self.source/'README').read_text(),'modified')

    def test_restore_cannot_overwrite_existing_directory(self):
        result=create_backup(self.source,self.runtime,self.output)
        with self.assertRaises(ToolError):restore_backup(result['archive'],self.runtime)
        self.assertTrue((self.runtime/'receipts/task.json').is_file())

    def test_symlink_destination_is_denied(self):
        result=create_backup(self.source,self.runtime,self.output)
        target=self.root/'link';target.symlink_to(self.root/'missing')
        with self.assertRaises(ToolError):restore_backup(result['archive'],target)

    def test_recursive_or_public_output_denied(self):
        nested=self.runtime/'backups';nested.mkdir(mode=0o700)
        with self.assertRaises(ToolError):create_backup(self.source,self.runtime,nested)
        self.output.chmod(0o755)
        with self.assertRaises(ToolError):create_backup(self.source,self.runtime,self.output)

    def crafted(self,names,*,kind=None):
        path=self.root/'bad.tar.gz'
        with tarfile.open(path,'w:gz') as tar:
            data=json.dumps({'schema':'KMCW_PORTABLE_BACKUP_V1','files':{}}).encode()
            item=tarfile.TarInfo('manifest.json');item.size=len(data);tar.addfile(item,io.BytesIO(data))
            for name in names:
                item=tarfile.TarInfo(name)
                if kind:item.type=kind;item.linkname='/etc/passwd'
                tar.addfile(item,io.BytesIO(b''))
        return path

    def test_archive_traversal_absolute_symlink_hardlink_denied(self):
        for name,kind in [('../escape',None),('/escape',None),('x',tarfile.SYMTYPE),
                          ('x',tarfile.LNKTYPE),('x\\escape',None),('./x',None)]:
            with self.subTest(name=name),self.assertRaises(ToolError):
                verify_backup(self.crafted([name],kind=kind))

    def test_unmanifested_and_duplicate_entries_denied(self):
        for names in [['x'],['x','x']]:
            with self.assertRaises(ToolError):verify_backup(self.crafted(names))

    def test_bad_database_service_never_executes(self):
        with self.assertRaises(ToolError):
            create_backup(self.source,self.runtime,self.output,pg_service='dbname=other')


if __name__=='__main__':unittest.main()
