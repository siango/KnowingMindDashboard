import {test} from 'node:test';
import assert from 'node:assert/strict';
import {handleStripeWebhook,verifyStripeEvent} from './stripe-webhook.mjs';
const secret='whsec_synthetic_fixture',acct='acct_synthetic_fixture';
const event={id:'evt_test123',type:'invoice.paid',livemode:false,created:1780000000,data:{object:{id:'in_test123'}}};
const env0={KMCP_STRIPE_INBOX_ENABLED:'YES',STRIPE_WEBHOOK_SECRET:secret,STRIPE_ACCOUNT_ID:acct,STRIPE_MODE:'test'};
async function req(e=event,key=secret,ts=Math.floor(Date.now()/1000)){
 const body=JSON.stringify(e),msg=new TextEncoder().encode(ts+'.'+body);
 const h=await crypto.subtle.importKey('raw',new TextEncoder().encode(key),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 const sig=Array.from(new Uint8Array(await crypto.subtle.sign('HMAC',h,msg)),x=>x.toString(16).padStart(2,'0')).join('');
 return new Request('https://example.invalid/stripe/webhook',{method:'POST',headers:{'stripe-signature':'t='+ts+',v1='+sig},body});
}
function fakeDB(){
 const rows=new Map(),out=new Set();
 return {rows,out,prepare(sql){return{bind(...p){return{
 async run(){if(sql.includes('INSERT INTO kmcp_stripe_event_inbox')&&!rows.has(p[0]))rows.set(p[0],{body_sha256:p[3],stripe_account_id:p[4],livemode:p[5]});
 if(sql.includes('INSERT INTO kmcp_stripe_event_outbox')&&rows.has(p[0]))out.add(p[0]);},
 async first(){return rows.get(p[0])||null;}
 }}}},async batch(stmts){for(const s of stmts)await s.run();}};
}
test('no signing secret or D1 -> 503',async()=>assert.equal((await handleStripeWebhook(await req(),{})).status,503));
test('tamper, stale, unsigned, mismatched environment denied',async()=>{
 const signed=await req(),raw=new Uint8Array(await signed.arrayBuffer());
 assert.equal((await verifyStripeEvent(raw,signed.headers.get('stripe-signature'),secret)).id,event.id);
 await assert.rejects(()=>verifyStripeEvent(raw,signed.headers.get('stripe-signature'),secret,Math.floor(Date.now()/1000)+3600));
 const d=fakeDB();
 assert.equal((await handleStripeWebhook(await req(event,'whsec_other'),{...env0,DB:d})).status,400);
 assert.equal((await handleStripeWebhook(await req({...event,livemode:true}),{...env0,DB:d})).status,403);
 assert.equal(d.rows.size,0);
});
test('valid event stored exactly once, only reconciliation queued; conflicting replay denied',async()=>{
 const d=fakeDB(),env={...env0,DB:d};
 assert.equal((await handleStripeWebhook(await req(),env)).status,200);
 assert.equal((await handleStripeWebhook(await req(),env)).status,200);
 assert.equal(d.rows.size,1);assert.equal(d.out.size,1);
 assert.equal((await handleStripeWebhook(await req({...event,type:'invoice.payment_failed'}),env)).status,409);
});
test('DB failure fail closed, no payment/entitlement granted',async()=>{
 const db={prepare(){return{bind(){return{run(){throw Error('offline')},first(){return null}}}}},batch(){throw Error('offline')}};
 assert.equal((await handleStripeWebhook(await req(),{...env0,DB:db})).status,503);
});
