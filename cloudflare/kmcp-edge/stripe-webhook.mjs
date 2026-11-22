// Stripe-signed inbox for the original Cloudflare KMCP Edge; NO entitlement execution.
const accepted=new Set(['checkout.session.completed','checkout.session.async_payment_succeeded','checkout.session.async_payment_failed','customer.subscription.created','customer.subscription.updated','customer.subscription.deleted','invoice.paid','invoice.payment_failed','charge.refunded','charge.dispute.created','charge.dispute.updated','charge.dispute.closed','radar.early_fraud_warning.created']);
const reply=(code,state)=>new Response(JSON.stringify({ok:code===200,status:state}),{status:code,headers:{'content-type':'application/json','cache-control':'no-store'}});
const safe=x=>typeof x==='string'&&/^[A-Za-z0-9_:-]{4,180}$/.test(x);
const hex=bytes=>Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
function equal(a,b){if(!/^[a-f0-9]{64}$/.test(a||'')||!b||b.length!==64)return false;let d=0;for(let i=0;i<64;i++)d|=a.charCodeAt(i)^b.charCodeAt(i);return d===0;}
export async function verifyStripeEvent(bytes,signature,secret,now=Math.floor(Date.now()/1000)){
 if(!(bytes instanceof Uint8Array)||bytes.byteLength<2||bytes.byteLength>131072||typeof secret!=='string'||!secret.startsWith('whsec_')||typeof signature!=='string'||signature.length>2048)throw Error('INVALID_SIGNATURE_INPUT');
 const parts=signature.split(',').map(s=>s.trim().split('='));
 const t=parts.filter(x=>x.length===2&&x[0]==='t');
 const signatures=parts.filter(x=>x.length===2&&x[0]==='v1'&&/^[a-f0-9]{64}$/.test(x[1]));
 if(t.length!==1||!/^\d{10}$/.test(t[0][1])||signatures.length===0||Math.abs(Number(t[0][1])-now)>300)throw Error('INVALID_STRIPE_TIMESTAMP');
 const message=new Uint8Array(new TextEncoder().encode(t[0][1]+'.').length+bytes.length);
 const prefix=new TextEncoder().encode(t[0][1]+'.');message.set(prefix);message.set(bytes,prefix.length);
 const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 const computed=hex(new Uint8Array(await crypto.subtle.sign('HMAC',key,message)));
 if(!signatures.some(x=>equal(x[1],computed)))throw Error('INVALID_STRIPE_SIGNATURE');
 let event;try{event=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes))}catch{throw Error('INVALID_STRIPE_JSON')}
 if(!event||!safe(event.id)||!event.id.startsWith('evt_')||typeof event.type!=='string'||!Number.isSafeInteger(event.created)||typeof event.livemode!=='boolean'||!event.data||!event.data.object||!safe(event.data.object.id))throw Error('INVALID_STRIPE_EVENT');
 return event;
}
export async function handleStripeWebhook(request,env){
 if(request.method!=='POST')return reply(405,'POST_REQUIRED');
 if(!env||env.KMCP_STRIPE_INBOX_ENABLED!=='YES'||!env.STRIPE_WEBHOOK_SECRET||!env.DB||!safe(env.STRIPE_ACCOUNT_ID)||!env.STRIPE_ACCOUNT_ID.startsWith('acct_')||!['live','test'].includes(env.STRIPE_MODE))return reply(503,'WEBHOOK_NOT_CONFIGURED');
 if(Number(request.headers.get('content-length')||0)>131072)return reply(413,'OVERSIZED_EVENT');
 let bytes;try{bytes=new Uint8Array(await request.arrayBuffer())}catch{return reply(400,'INVALID_BODY')}
 let event;try{event=await verifyStripeEvent(bytes,request.headers.get('stripe-signature'),env.STRIPE_WEBHOOK_SECRET)}catch{return reply(400,'INVALID_SIGNATURE_OR_EVENT')}
 if(event.livemode!==(env.STRIPE_MODE==='live')||event.account&&event.account!==env.STRIPE_ACCOUNT_ID)return reply(403,'ACCOUNT_OR_MODE_MISMATCH');
 const state=accepted.has(event.type)?'PENDING_RECONCILIATION':'IGNORED_NOT_SUBSCRIBED';
 const digest=hex(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)));
 const insert='INSERT INTO kmcp_stripe_event_inbox (event_id,event_type,object_id,body_sha256,stripe_account_id,livemode,event_created,processing_state,received_at) VALUES (?,?,?,?,?,?,?,?,datetime(\'now\')) ON CONFLICT(event_id) DO NOTHING';
 const outbox='INSERT INTO kmcp_stripe_event_outbox(event_id,route,status,created_at) SELECT event_id,'KMCO_OBSERVE_ONLY','PENDING',datetime(\'now\') FROM kmcp_stripe_event_inbox WHERE event_id=? AND processing_state=\'PENDING_RECONCILIATION\' ON CONFLICT(event_id) DO NOTHING';
 try{
  await env.DB.batch([
   env.DB.prepare(insert).bind(event.id,event.type,event.data.object.id,digest,env.STRIPE_ACCOUNT_ID,event.livemode?1:0,event.created,state),
   env.DB.prepare(outbox).bind(event.id)
  ]);
  const saved=await env.DB.prepare('SELECT body_sha256,stripe_account_id,livemode FROM kmcp_stripe_event_inbox WHERE event_id=?').bind(event.id).first();
  if(!saved||saved.body_sha256!==digest||saved.stripe_account_id!==env.STRIPE_ACCOUNT_ID||Number(saved.livemode)!==(event.livemode?1:0))return reply(409,'STRIPE_EVENT_ID_CONFLICT');
 }catch{return reply(503,'D1_WRITE_FAILED')}
 return reply(200,state);
}
