// Multi-client KMCP authorization/metering policy. SOURCE ONLY, not deployed.
// Called only AFTER Cloudflare Access JWT signature, issuer, audience, expiry
// and actor membership have been validated against the existing D1 authority.
// No raw client token or unverified email may be a principal.
const READ_METHODS = new Set(['initialize','ping','tools/list','tools/call']);
const BAD_ID = /[^a-zA-Z0-9_.:@/-]/;
function identifier(value, name) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 160 || BAD_ID.test(value))
    throw new Error(name + '_INVALID');
  return value;
}
export function resolveMcpPrincipal({verifiedAccess, membership, channel}) {
  if (!verifiedAccess || verifiedAccess.verified !== true)
    throw new Error('SIGNED_ACCESS_IDENTITY_REQUIRED');
  const issuer=identifier(verifiedAccess.issuer,'ACCESS_ISSUER');
  const subject=identifier(verifiedAccess.subject,'ACCESS_SUBJECT');
  if (!verifiedAccess.audienceVerified || !verifiedAccess.signatureVerified || !verifiedAccess.expiryVerified)
    throw new Error('ACCESS_JWT_VERIFICATION_INCOMPLETE');
  if (!membership || membership.status !== 'ACTIVE' || membership.verifiedIdentityKey !== issuer + '|' + subject)
    throw new Error('IDENTITY_MEMBERSHIP_UNBOUND');
  const tenantId=identifier(membership.company_id,'TENANT');
  const userId=identifier(membership.user_id,'USER');
  const allowedChannels=['CHATGPT','WEB','CLI','SSH','LINE','FB','AGENT'];
  if (!allowedChannels.includes(channel) || !Array.isArray(membership.channels) ||
      !membership.channels.includes(channel))
    throw new Error('CHANNEL_NOT_GRANTED');
  return Object.freeze({tenantId,userId,channel,role:identifier(membership.role,'ROLE')});
}
// Server-side adapter seam: the injected reader MUST be the existing authoritative
// membership/identity lookup. Never construct its rows from HTTP request data.
// Until that reader is bound, unknown/ambiguous identities remain denied.
export async function resolveMcpPrincipalFromAuthority({verifiedAccess,channel,readMembership}){
  if (!verifiedAccess || verifiedAccess.verified!==true ||
      verifiedAccess.signatureVerified!==true || verifiedAccess.audienceVerified!==true ||
      verifiedAccess.expiryVerified!==true)
    throw new Error('ACCESS_JWT_VERIFICATION_INCOMPLETE');
  const issuer=identifier(verifiedAccess.issuer,'ACCESS_ISSUER');
  const subject=identifier(verifiedAccess.subject,'ACCESS_SUBJECT');
  if (typeof readMembership!=='function') throw new Error('IDENTITY_AUTHORITY_NOT_BOUND');
  const rows=await readMembership(Object.freeze({issuer,subject,channel}));
  if (!Array.isArray(rows) || rows.length!==1) throw new Error('IDENTITY_MEMBERSHIP_UNBOUND_OR_AMBIGUOUS');
  return resolveMcpPrincipal({verifiedAccess,membership:rows[0],channel});
}
export function authorizeMcpTool({principal,method,tool,grant,entitlement,quantity=1}) {
  if (!principal || !READ_METHODS.has(method)) throw new Error('MCP_METHOD_DENIED');
  if (method !== 'tools/call') return {allow:true, billable:false};
  identifier(tool,'TOOL');
  if (!grant || grant.company_id !== principal.tenantId ||
      grant.user_id !== principal.userId || grant.tool !== tool ||
      grant.status !== 'ACTIVE' || grant.permission !== 'read' ||
      grant.channel !== principal.channel)
    throw new Error('EXACT_READ_GRANT_REQUIRED');
  if (!entitlement || entitlement.company_id !== principal.tenantId ||
      entitlement.status !== 'ACTIVE' || !Array.isArray(entitlement.tools) ||
      !entitlement.tools.includes(tool))
    throw new Error('TOOL_ENTITLEMENT_REQUIRED');
  if (!Number.isSafeInteger(quantity) || quantity < 1 || quantity > 1000)
    throw new Error('INVALID_USAGE_QUANTITY');
  if (!Number.isSafeInteger(entitlement.limit) || entitlement.limit < 0 ||
      !Number.isSafeInteger(entitlement.used) || entitlement.used < 0 ||
      entitlement.used + quantity > entitlement.limit)
    throw new Error('MCP_QUOTA_EXCEEDED');
  return {allow:true,billable:entitlement.billable===true,metric:'MCP_READ_CALL',quantity};
}
export function completedMcpUsage({principal,tool,executionId,result,evidenceRef,decision,occurredAt}) {
  if (!decision || decision.allow !== true || !principal ||
      !result || result.ok !== true || result.isError === true)
    throw new Error('USAGE_REQUIRES_SUCCESSFUL_AUTHORIZED_CALL');
  const exec=identifier(executionId,'EXECUTION_ID');
  const evidence=identifier(evidenceRef,'EVIDENCE_REF');
  identifier(tool,'TOOL');
  if (typeof occurredAt !== 'string' || !/^\d{4}-\d\d-\d\dT/.test(occurredAt))
    throw new Error('USAGE_TIMESTAMP_INVALID');
  const key=[principal.tenantId,principal.userId,principal.channel,tool,exec].join(':');
  // Persist with UNIQUE idempotency_key using existing company_os_usage_ledger.
  // Return this receipt only once after authoritative D1 commit; source-level
  // construction is NOT a charge and does NOT assert that persistence occurred.
  return Object.freeze({
    company_id:principal.tenantId,user_id:principal.userId,metric:'MCP_READ_CALL',
    quantity:decision.quantity || 1,cost_class:decision.billable?'METERED':'INTERNAL',
    idempotency_key:key,source_ref:'KMCP_ACCESS_GATE_V1',evidence_ref:evidence,
    occurred_at:occurredAt, billing_status:'UNBILLED_PENDING_RECONCILIATION'
  });
}
export function billingExportAllowed({usage,entitlement,customer,verifiedInvoiceContract}) {
  // Never bill a raw HTTP request or failed/replayed call.
  return Boolean(usage && entitlement && customer && verifiedInvoiceContract &&
      usage.billing_status==='PERSISTED_VERIFIED' &&
      usage.cost_class==='METERED' &&
      entitlement.status==='ACTIVE' && entitlement.billing_enabled===true &&
      customer.verified===true && customer.tenantId===usage.company_id &&
      verifiedInvoiceContract.verified===true);
}
