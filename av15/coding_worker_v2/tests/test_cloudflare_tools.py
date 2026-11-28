import io
import json
import unittest
from urllib import error
from coding_worker_v2.agent.cloudflare_tools import CloudflareClient
from coding_worker_v2.agent.tools import ToolError, build_default_registry

ACCOUNT = "a" * 32
MODEL = "@cf/example/small"

class CloudflareTools(unittest.TestCase):
    def client(self, result=None, info=None):
        self.calls = []
        def opened(req, timeout):
            self.calls.append(req)
            return io.BytesIO(json.dumps({"success":True,"result":result,"result_info":info}).encode())
        return CloudflareClient(ACCOUNT, lambda:"fixture-token-no-real-credential",
                                allowed_models=(MODEL,),opener=opened)

    def test_opt_in_preserves_default_and_adds_three_typed_tools(self):
        self.assertEqual(len(build_default_registry(circuits=True).names()),73)
        reg=build_default_registry(circuits=True,cloudflare=self.client([]))
        self.assertEqual(len(reg.names()),76)
        specs={x['name']:x for x in reg.command_contract()['commands']}
        self.assertEqual(specs['cf.ai.run']['permission'],'write')
        self.assertEqual(specs['cf.inventory.read']['permission'],'read')

    def test_metadata_redacts_unlisted_provider_fields(self):
        client=self.client([{'id':'worker','bindings':[{'secret':'fixture'}],
                             'environment':{'token':'fixture'},'name':'safe'}])
        result=client.inventory('workers')
        self.assertEqual(result['items'],[{'id':'worker'}])
        self.assertNotIn('fixture',json.dumps(result))
        self.assertEqual(self.calls[0].full_url,
                         'https://api.cloudflare.com/client/v4/accounts/'+ACCOUNT+'/workers/scripts')

    def test_page_is_validated_before_network(self):
        client=self.client([])
        for page in (0,11,True,'2'):
            with self.subTest(page=page),self.assertRaises(ToolError):
                client.inventory('d1',page=page)
        self.assertEqual(self.calls,[])

    def test_unknown_service_cannot_choose_an_endpoint(self):
        client=self.client([])
        with self.assertRaises(ToolError): client.inventory('../tokens')
        self.assertEqual(self.calls,[])

    def test_r2_nested_bucket_result(self):
        result=self.client({'buckets':[{'name':'existing','secret':'fixture'}]}).inventory('r2')
        self.assertEqual(result['items'],[{'name':'existing'}])
        self.assertFalse(result['complete'])

    def test_paginated_inventory_does_not_claim_complete(self):
        result=self.client([{'name':'one'}],{'total_count':101,'total_pages':2}).inventory('d1')
        self.assertFalse(result['complete'])
        result=self.client([{'name':'one'}],{'total_count':1,'total_pages':1}).inventory('d1')
        self.assertTrue(result['complete'])

    def test_single_page_services_do_not_accept_other_pages(self):
        client=self.client([])
        with self.assertRaises(ToolError): client.inventory('containers',page=2)
        self.assertEqual(self.calls,[])

    def test_inference_requires_model_and_bounded_input(self):
        client=self.client({'response':'OK'})
        for model,prompt,tokens in [(MODEL,'a',65),(MODEL,'',8),(MODEL,'a'*2049,8),
                                    ('@cf/other/unapproved','a',8),(MODEL,'a',True)]:
            with self.subTest(tokens=tokens),self.assertRaises(ToolError):
                client.infer(model,prompt,max_tokens=tokens)
        self.assertEqual(self.calls,[])

    def test_actual_inference_request_is_bounded(self):
        client=self.client({'response':'OK','provider_secret':'fixture'})
        result=client.infer(MODEL,'Reply OK',max_tokens=8)
        self.assertEqual(result['response'],'OK')
        self.assertIsNone(result['billed_cost'])
        self.assertNotIn('provider_secret',result)
        self.assertEqual(json.loads(self.calls[0].data)['max_tokens'],8)
        self.assertEqual(self.calls[0].get_method(),'POST')

    def test_live_chat_completion_shape_is_normalized(self):
        result=self.client({'choices':[{'message':{'content':'OK','reasoning_content':'private'}}]}).infer(MODEL,'Reply OK')
        self.assertEqual(result['response'],'OK')
        self.assertNotIn('private',json.dumps(result))

    def test_empty_and_ambiguous_completions_fail_closed(self):
        for payload in ({'response':''},{'choices':[]},
                        {'choices':[{'message':{'content':None}}]},
                        {'choices':[{'message':{'content':'A'}},{'message':{'content':'B'}}]}):
            with self.subTest(payload=payload),self.assertRaises(ToolError):
                self.client(payload).infer(MODEL,'Reply OK')

    def test_provider_failure_never_returns_sensitive_error_body(self):
        def opened(req,timeout): raise error.HTTPError(req.full_url,403,'fixture-secret',{},None)
        client=CloudflareClient(ACCOUNT,lambda:'fixture-token-no-real-credential',opener=opened)
        with self.assertRaises(ToolError) as exc: client.inventory('workers')
        self.assertEqual(exc.exception.reason,'CF_SCOPE_DENIED')
        self.assertNotIn('fixture-secret',str(exc.exception))

    def test_bad_account_and_bad_token_fail_closed(self):
        with self.assertRaises(ToolError): CloudflareClient('../bad',lambda:'x')
        client=CloudflareClient(ACCOUNT,lambda:'bad\nheader',opener=lambda *a,**k:None)
        with self.assertRaises(ToolError): client.inventory('workers')

    def test_malformed_response_is_not_success(self):
        client=self.client({'wrong':'shape'})
        with self.assertRaises(ToolError): client.inventory('workers')

if __name__=='__main__':
    unittest.main()
