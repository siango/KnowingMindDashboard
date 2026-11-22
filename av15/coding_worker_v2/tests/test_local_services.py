import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from coding_worker_v2.agent.tools import ToolError, build_default_registry
from coding_worker_v2.agent.owner_cli import execute


class FixturePhoto:
    def __init__(self):
        self.tools={n:{} for n in ('session_list','doc_inspect','doc_new','doc_export')}
        self.closed=False;self.calls=[]
    def call_tool(self,name,args):
        self.calls.append((name,args));return {'ok':True,'fixture_only':True}
    def close(self):self.closed=True


class LocalServicesTests(unittest.TestCase):
    def test_artcraft_write_requires_typed_native_parent_grant(self):
        photo=FixturePhoto();reg=build_default_registry(photocraft=photo)
        with self.assertRaises(ToolError):
            reg.call('creative.photocraft.create',{'name':'a','width':64,'height':64},
                     {'artcraft_write_enabled':True})
        self.assertEqual(photo.calls,[])

    def test_opt_in_native_circuit_uses_original_approval_and_closes_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';repo.mkdir();base=root/'runtime'
            (base/'queue').mkdir(parents=True)
            for args in [('init','-q'),('config','user.name','Fixture'),
                         ('config','user.email','fixture@example.invalid')]:
                subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
            (repo/'README').write_text('fixture')
            subprocess.run(['git','-C',str(repo),'add','.'],check=True,capture_output=True)
            subprocess.run(['git','-C',str(repo),'commit','-qm','fixture'],check=True,capture_output=True)
            cfg=root/'runtime.json';cfg.write_text(json.dumps({'schema':'KMCP_RUNTIME_V1',
                'node_id':'fixture','account_id':'posix:'+str(os.getuid()),
                'mission_id':'fixture','task_id':'fixture','workspace':str(repo),
                'runtime_base':str(base),'photocraft':True,'audit_all_tools':True}));cfg.chmod(0o600)
            graph={'nodes':[{'id':'new','tool':'creative.photocraft.create',
                'arguments':{'name':'fixture','width':64,'height':64},'after':[],
                'result_schema':{'type':'object'}}],
                'outputs':{'created':{'$ref':'new#'}},
                'acceptance':[{'path':'/created','schema':{'type':'object'}}]}
            photo=FixturePhoto()
            with patch('coding_worker_v2.agent.tools.PhotoCraftStdio',return_value=photo):
                result=execute(cfg,graph,'fixture create','request1',approved=True)
            self.assertEqual(result['result']['status'],'COMPLETED')
            self.assertEqual([name for name,_ in photo.calls],['doc_new'])
            self.assertTrue(photo.closed)

    def test_public_mcp_cannot_use_private_artcraft_opt_in_as_permission(self):
        photo=FixturePhoto();reg=build_default_registry(photocraft=photo)
        with self.assertRaises(ToolError) as exc:
            reg.call('creative.photocraft.create',{'name':'a','width':64,'height':64},
                     {'artcraft_write_enabled':True},mcp=True)
        self.assertEqual(exc.exception.reason,'MCP_TOOL_SCOPE_DENIED')
        self.assertEqual(photo.calls,[])


if __name__=='__main__':unittest.main()
