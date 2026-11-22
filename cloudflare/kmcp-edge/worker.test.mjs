import { test } from 'node:test';
import assert from 'node:assert/strict';
import edge from './worker.mjs';
const env = { EDGE_TOKEN: 'edge-fixture', ORIGIN_AUTHORIZATION: 'Bearer origin-fixture', ORIGIN_URL: 'https://origin.example/mcp', CF_ACCESS_CLIENT_ID: 'id-fixture', CF_ACCESS_CLIENT_SECRET: 'secret-fixture' };
const req = (path='/mcp', extra={}) => new Request('https://edge.example'+path, { headers: { Authorization: 'Bearer edge-fixture' }, ...extra });
test('health makes no claim of origin readiness', async () => {
  assert.deepEqual(await (await edge.fetch(req('/health'), {})).json(), {service:'kmcp-edge',originChecked:false});
});
test('unconfigured edge fails closed', async () => assert.equal((await edge.fetch(req(), {})).status, 503));
test('unauthorized request never reaches origin', async () => assert.equal((await edge.fetch(req('/mcp',{headers:{}}),env)).status,401));
test('only the exact MCP route and supported verbs are accepted', async () => {
  assert.equal((await edge.fetch(req('/admin'),env)).status,404);
  assert.equal((await edge.fetch(req('/mcp',{method:'PUT'}),env)).status,405);
});
test('invalid origin fails before any outgoing request', async () => {
  for(const url of ['http://origin.example/mcp','https://a:b@origin.example/mcp','https://origin.example/admin','https://origin.example/mcp?secret=x'])
    assert.equal((await edge.fetch(req(),{...env,ORIGIN_URL:url})).status,503);
});
test('streams body and session headers while replacing client credentials', async () => {
  const original=globalThis.fetch;
  globalThis.fetch=async (url, options) => {
    assert.equal(String(url),'https://origin.example/mcp?x=1');
    assert.equal(options.headers.get('Authorization'),'Bearer origin-fixture');
    assert.equal(options.headers.get('CF-Access-Client-Secret'),'secret-fixture');
    assert.equal(options.headers.get('cookie'),null);
    assert.equal(options.headers.get('Mcp-Session-Id'),'session-fixture');
    assert.equal(options.redirect,'manual');
    assert.equal(await new Response(options.body).text(),'payload');
    return new Response('data: fixture\n\n',{headers:{'Content-Type':'text/event-stream','Mcp-Session-Id':'session-fixture','Set-Cookie':'no=1'}});
  };
  try {
    const response=await edge.fetch(req('/mcp?x=1',{method:'POST',body:'payload',headers:{Authorization:'Bearer edge-fixture',Cookie:'client=1','Mcp-Session-Id':'session-fixture','CF-Access-Client-Secret':'spoof'}}),env);
    assert.equal(await response.text(),'data: fixture\n\n');
    assert.equal(response.headers.get('Set-Cookie'),null);
    assert.equal(response.headers.get('Mcp-Session-Id'),'session-fixture');
    assert.equal(response.headers.get('Cache-Control'),'no-store');
  } finally {globalThis.fetch=original;}
});
test('Access redirects and network failures produce a bounded error', async () => {
  const original=globalThis.fetch;
  try {
    globalThis.fetch=async()=>new Response(null,{status:302,headers:{Location:'https://login.example'}});
    assert.equal((await edge.fetch(req(),env)).status,502);
    globalThis.fetch=async()=>{throw Error('secret upstream details');};
    const r=await edge.fetch(req(),env); assert.equal(r.status,502); assert.equal(await r.text(),'Origin unavailable');
  } finally {globalThis.fetch=original;}
});
