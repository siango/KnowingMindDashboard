import {test} from 'node:test';
import assert from 'node:assert/strict';
import {adaptCompanyOsReadGrant,resolveMcpPrincipal,resolveMcpPrincipalFromAuthority,authorizeMcpTool,completedMcpUsage,billingExportAllowed} from './tenant-policy.mjs';
const verifiedAccess={verified:true,signatureVerified:true,audienceVerified:true,expiryVerified:true,issuer:'https://access.example',subject:'user-123'};
const membership={verifiedIdentityKey:'https://access.example|user-123',company_id:'tenant-A',user_id:'user-123',role:'COMPANY_VIEWER',status:'ACTIVE',channels:['CHATGPT','WEB']};
const principal=resolveMcpPrincipal({verifiedAccess,membership,channel:'CHATGPT'});
const grant={company_id:'tenant-A',user_id:'user-123',tool:'project.readback',channel:'CHATGPT',permission:'read',status:'ACTIVE'};
const entitlement={company_id:'tenant-A',status:'ACTIVE',tools:['project.readback'],limit:10,used:9,billable:true};
test('signed issuer+subject mapped into explicit tenant/channel',()=>{
  assert.equal(principal.tenantId,'tenant-A');
  assert.notEqual(resolveMcpPrincipal({...{verifiedAccess,membership},channel:'WEB'}).channel,principal.channel);
});
test('unverified JWT/unknown external identity never resolves',()=>{
  assert.throws(()=>resolveMcpPrincipal({verifiedAccess:{...verifiedAccess,signatureVerified:false},membership,channel:'CHATGPT'}),/ACCESS_JWT_VERIFICATION_INCOMPLETE/);
  assert.throws(()=>resolveMcpPrincipal({verifiedAccess,membership:{...membership,verifiedIdentityKey:'wrong'},channel:'CHATGPT'}),/IDENTITY_MEMBERSHIP_UNBOUND/);
  assert.throws(()=>resolveMcpPrincipal({verifiedAccess,membership,channel:'CLI'}),/CHANNEL_NOT_GRANTED/);
});
test('per-tool, channel and tenant crossovers fail closed',()=>{
  assert.throws(()=>authorizeMcpTool({principal,method:'tools/call',tool:'project.readback',grant:{...grant,company_id:'tenant-B'},entitlement}),/EXACT_READ_GRANT_REQUIRED/);
  assert.throws(()=>authorizeMcpTool({principal,method:'tools/call',tool:'project.readback',grant:{...grant,channel:'WEB'},entitlement}),/EXACT_READ_GRANT_REQUIRED/);
  assert.throws(()=>authorizeMcpTool({principal,method:'tools/call',tool:'db.write',grant:{...grant,tool:'db.write',permission:'write'},entitlement}),/EXACT_READ_GRANT_REQUIRED/);
});
test('quota hard stop before tool execution',()=>{
  assert.equal(authorizeMcpTool({principal,method:'tools/call',tool:'project.readback',grant,entitlement}).billable,true);
  assert.throws(()=>authorizeMcpTool({principal,method:'tools/call',tool:'project.readback',grant,entitlement:{...entitlement,used:10}}),/MCP_QUOTA_EXCEEDED/);
  assert.throws(()=>authorizeMcpTool({principal,method:'unknown',tool:'project.readback'}),/MCP_METHOD_DENIED/);
});
test('usage constructed only for successful bound call, never automatically charged',()=>{
  const decision=authorizeMcpTool({principal,method:'tools/call',tool:'project.readback',grant,entitlement});
  const args={principal,tool:'project.readback',executionId:'receipt-1',result:{ok:true},evidenceRef:'sha256:abc',decision,occurredAt:'2026-11-23T00:00:00Z'};
  assert.throws(()=>completedMcpUsage({...args,result:{ok:false}}),/USAGE_REQUIRES_SUCCESSFUL_AUTHORIZED_CALL/);
  const entry=completedMcpUsage(args);
  assert.equal(entry.company_id,'tenant-A');
  assert.equal(entry.billing_status,'UNBILLED_PENDING_RECONCILIATION');
  assert.equal(billingExportAllowed({usage:entry,entitlement:{...entitlement,billing_enabled:true},customer:{verified:true,tenantId:'tenant-A'},verifiedInvoiceContract:{verified:true}}),false);
  assert.equal(billingExportAllowed({usage:{...entry,billing_status:'PERSISTED_VERIFIED'},entitlement:{...entitlement,billing_enabled:true},customer:{verified:true,tenantId:'tenant-A'},verifiedInvoiceContract:{verified:true}}),true);
  assert.equal(billingExportAllowed({usage:{...entry,billing_status:'PERSISTED_VERIFIED'},entitlement:{...entitlement,billing_enabled:true},customer:{verified:true,tenantId:'tenant-B'},verifiedInvoiceContract:{verified:true}}),false);
});

test('authority lookup uses only verified issuer and subject, denies unavailable or ambiguous mapping',async()=>{
  const args={verifiedAccess,channel:'WEB'};
  await assert.rejects(resolveMcpPrincipalFromAuthority(args),/IDENTITY_AUTHORITY_NOT_BOUND/);
  let called=0;
  const readMembership=async key=>{called++;assert.deepEqual(key,{issuer:verifiedAccess.issuer,subject:verifiedAccess.subject,channel:'WEB'});return [membership];};
  await assert.rejects(resolveMcpPrincipalFromAuthority({...args,verifiedAccess:{...verifiedAccess,signatureVerified:false},readMembership}),/ACCESS_JWT_VERIFICATION_INCOMPLETE/);
  assert.equal(called,0);
  assert.equal((await resolveMcpPrincipalFromAuthority({...args,readMembership})).userId,membership.user_id);
  for(const rows of [[],[membership,membership],[{...membership,status:'REVOKED'}],[{...membership,verifiedIdentityKey:'wrong'}]]){
    await assert.rejects(resolveMcpPrincipalFromAuthority({...args,readMembership:async()=>rows}),/IDENTITY_MEMBERSHIP_UNBOUND/);
  }
});

test('existing Company OS grant never implies MCP access without explicit tool and channel policy',()=>{
 const row={enabled:1,company_id:principal.tenantId,tool_id:'project.readback',roles_json:JSON.stringify([principal.role]),capabilities_json:'["MCP_READ:project.readback"]',actions_json:'["MCP_READ:CHATGPT"]',risk_policy_ref:'KMCP_READ_ONLY_V1',source_ref:'fixture',evidence_ref:'fixture'};
 const grant=adaptCompanyOsReadGrant({principal,tool:'project.readback',row});
 assert.equal(authorizeMcpTool({principal,method:'tools/call',tool:'project.readback',grant,entitlement}).allow,true);
 for(const delta of [{enabled:0},{company_id:'other'},{actions_json:'["STATUS_CHECK"]'},{capabilities_json:'["RUN_ALLOWED_REVERSIBLE_WORK"]'},{roles_json:'[]'},{risk_policy_ref:'other'},{evidence_ref:null}]){
  assert.throws(()=>adaptCompanyOsReadGrant({principal,tool:'project.readback',row:{...row,...delta}}),/EXACT_READ_GRANT_REQUIRED/);
 }
});
