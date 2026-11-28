import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from coding_worker_v2.agent.ssh_cli import converse, worker_main
from coding_worker_v2.agent.native_cloudflare import private_file
from coding_worker_v2.agent.cloudflare_tools import CloudflareClient
from coding_worker_v2.agent.tools import ToolError, build_default_registry


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {'schema':'KMCW_SSH_V1','provider':{'account_id':'a'*32},
                       'model':'@cf/example/small','sessions':str(self.root/'sessions')}
        self.calls = []
        def opened(req, timeout):
            self.calls.append(json.loads(req.data))
            return io.BytesIO(json.dumps({'success':True,'result':{
                'choices':[{'message':{'content':'ข้อเสนอจากหลักฐาน'}}]}}).encode())
        self.client = CloudflareClient('a'*32,lambda:'fixture-credential-123456',
                                      allowed_models=(self.config['model'],),opener=opened)

    def test_session_resume_and_idempotency(self):
        first = converse(self.config,'session','ตรวจงาน','r1',client=self.client)
        self.assertEqual(first['execution'],'NOT_REQUESTED')
        again = converse(self.config,'session','ตรวจงาน','r1',client=self.client)
        self.assertTrue(again['reused']); self.assertEqual(len(self.calls),1)
        converse(self.config,'session','ทำต่ออย่างไร','r2',client=self.client)
        self.assertEqual(len(self.calls[1]['messages']),4)
        self.assertEqual((self.root/'sessions/session.json').stat().st_mode & 0o777,0o600)

    def test_conflicting_request_is_not_replayed(self):
        converse(self.config,'session','หนึ่ง','r1',client=self.client)
        with self.assertRaises(ToolError):
            converse(self.config,'session','สอง','r1',client=self.client)
        self.assertEqual(len(self.calls),1)

    def test_ambiguous_inference_is_reserved_not_replayed(self):
        with patch.object(self.client,'converse',side_effect=ToolError('TEST_INTERRUPTION')):
            with self.assertRaises(ToolError): converse(self.config,'session','a','r1',client=self.client)
        with self.assertRaises(ToolError) as exc:
            converse(self.config,'session','a','r1',client=self.client)
        self.assertEqual(exc.exception.reason,'KMCW_AMBIGUOUS_REQUEST_NO_REPLAY')
        self.assertEqual(self.calls,[])

    def test_session_identity_must_not_cross_account(self):
        converse(self.config,'session','a','r1',client=self.client)
        self.config['provider']['account_id']='b'*32
        with self.assertRaises(ToolError): converse(self.config,'session','a','r2',client=self.client)

    def test_secret_is_redacted_before_model_and_disk(self):
        secret='github_pat_'+'A'*30
        converse(self.config,'session',secret,'r1',client=self.client)
        self.assertNotIn(secret,json.dumps(self.calls))
        self.assertNotIn(secret,(self.root/'sessions/session.json').read_text())

    def test_public_or_symlink_session_storage_denied(self):
        root=self.root/'sessions';root.mkdir(mode=0o755)
        with self.assertRaises(ToolError): converse(self.config,'session','a','r1',client=self.client)
        root.chmod(0o700)
        (root/'session.json').symlink_to(self.root/'outside')
        with self.assertRaises(ToolError): converse(self.config,'session','a','r1',client=self.client)

    def test_private_config_owner_permissions_and_fifo(self):
        path=self.root/'config';path.write_text('{}');path.chmod(0o600)
        self.assertEqual(private_file(path),{})
        path.chmod(0o644)
        with self.assertRaises(ToolError):private_file(path)
        fifo=self.root/'fifo';os.mkfifo(fifo,0o600)
        with self.assertRaises(ToolError):private_file(fifo)

    def test_conversation_bounds_and_tool_roles_denied(self):
        for messages,tokens in [([{'role':'tool','content':'a'}],8),
            ([{'role':'user','content':'a','tool_calls':[]}],8),
            ([{'role':'user','content':'a'},{'role':'system','content':'b'}],8),
            ([{'role':'user','content':'a'}],True),
            ([{'role':'user','content':'a'}],513)]:
            with self.subTest(messages=messages),self.assertRaises(ToolError):
                self.client.converse(self.config['model'],messages,max_tokens=tokens)
        self.assertEqual(self.calls,[])

    def test_native_mcp_inference_still_denies_missing_grant(self):
        reg=build_default_registry(cloudflare=self.client)
        with self.assertRaises(ToolError) as exc:
            reg.call('cf.ai.run',{'model':self.config['model'],'prompt':'OK'}, {},mcp=True)
        self.assertEqual(exc.exception.reason,'MCP_TOOL_SCOPE_DENIED')
        self.assertEqual(self.calls,[])

    def test_explicit_local_owner_inference_passes_original_policy(self):
        reg=build_default_registry(cloudflare=self.client)
        value=reg.call('cf.ai.run',{'model':self.config['model'],'prompt':'OK'},
                       {'workspace':str(self.root),'task_id':'canary'})
        self.assertEqual(value['response'],'ข้อเสนอจากหลักฐาน')

    def test_worker_does_not_create_implicit_package_queue(self):
        with patch.dict(os.environ,{},clear=True),patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(worker_main(['status']),1)


if __name__=='__main__':unittest.main()
