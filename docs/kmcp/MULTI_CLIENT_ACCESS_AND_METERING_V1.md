# KMCP Multi-Client Access and Usage Settlement V1

Status: **SOURCE IMPLEMENTATION / NOT DEPLOYED / NOT BILLING**. Mission: reuse existing KMCP V5 transport, KMCW V3 registry, Dhamma MCP, Cloudflare Access, KEBUNATE D1 operational authority, and the existing Company OS quota/usage ledger. Do not create a second MCP service, operational database, or canonical Dhamma store.

## Evidence and reused original components

- ChatGPT KnowingMindDhamma presently fails at protected `https://mcp.knowingmind.app/mcp` with HTTP 401. Current Ubuntu `kmcp-v5.service` directly exposes `kmcw_readiness` only; no verified external registry parity.
- `siango/kebunate/kcc/drizzle/0006_company_os_v2_free_scale_v1.sql` already defines `company_os_company`, `company_os_membership`, `company_os_plan_current`, `company_os_quota_current`, `company_os_usage_ledger` (globally unique `idempotency_key`) and `company_os_rate_limit_bucket`. Do not duplicate those tables.
- `siango/kebunate/registry/architecture/KACA_V1.json` describes existing Stripe provider, entitlement and credit-budget flow; Stripe is not the entitlement SSOT.
- New `cloudflare/kmcp-edge/tenant-policy.mjs` contains fail-closed pure policy for a *verified* Access principal, tenant membership, allowed client channel, exact read-only tool grant and plan quota, successful-call usage receipt and a separate verified billing-export gate. Companion tests `tenant-policy.test.mjs`. The module is not yet wired to the running HTTP worker or D1 and may not be described as production-ready.

## One canonical entry, many identities and clients

```
ChatGPT / Browser KMAA / CLI & SSH / LINE / FB / external agents
  -> existing Cloudflare Access application (Managed OAuth interactive; governed service token for machines)
  -> signed Cf-Access-Jwt-Assertion (verified issuer/audience/JWKS/exp)
  -> canonical identity map (issuer,sub -> existing Company OS user_id)
  -> membership (user_id,company_id) + allowed channel / device registration + tool grants
  -> existing KMCP V5 HTTP registry router (per-tool least privilege, no raw token forwarding)
  -> existing KMCW execution/authority, or delegated Dedicated Dhamma MCP
  -> success receipt -> D1 existing company_os_usage_ledger
  -> reconciled metered usage outbox -> existing Stripe entitlement/billing integration
```

Device or channel does **not** generate a new user, tenant or MCP runtime. A user's OAuth session can work across eligible devices; customer accounts/tenants remain isolated. Machines use separate scoped service identities with revocation, not a shared root bearer. An Access identity grants *transport* access only; KMCW permission and Dhamma authority remain independent.

## External standards to apply

- MCP Authorization 2026-07-28: OAuth protected resource metadata RFC 9728, authorization server discovery RFC 8414, Resource Indicators RFC 8707, signed/token validation and scope challenges.
- Cloudflare Managed OAuth for MCP server applications: Access user authorization, signed assertion delivered to origin; validate signed JWT (issuer, audience, signature, expiry); never substitute opaque OAuth bearer as Access assertion.
- Cloudflare Workers rate-limit bindings are for abuse control; an exact D1 tenant ledger is authoritative for account quota and billable events. Edge counters may be approximate or POP-scoped and are **not invoice truth**.
- Stripe Meters/Subscriptions/Checkout/Webhooks: reconcile customer mapping and signed webhook payment events. A meter event is not evidence of payment. Invoices and prices must be approved, displayed, and legally valid before external billing.

## Reuse-first schema delta (not applied)

Existing Company OS tables already cover membership, quota and usage. Only add *scoped* mappings to the same existing D1 after a reviewed migration:
1. `company_os_external_identity`: (identity_provider, issuer, subject_digest) unique -> user_id; map explicitly, never use unverified email or infer user_id equals `sub`.
2. `company_os_mcp_tool_grant`: company_id + user_id/service principal + client_channel + tool + permission + status; default no access, allow read-only Dhamma tools only after Dhamma domain agrees.
3. `company_os_mcp_billing_link`: company_id -> Stripe customer/subscription/price/meter identifiers and eligibility status; no raw keys.
4. `company_os_mcp_usage_export`: usage_id -> provider idempotency receipt + export status + reconciliation state; never a second usage ledger.

All four are proposals; confirm existing equivalents via D1 schema readback and reuse them before any migration. Never copy Dhamma Canon/verified private content to Company OS D1.

## Acceptance gates (must ALL pass before external activation)

G0: exact running Cloudflare Access app, zone/account, origin SHA and current token policy readback, rollback baseline, existing D1 schema.  
G1: interactive ChatGPT OAuth login on two independent devices/accounts + safe machine identity, signed assertion propagation and invalid/expired/revoked JWT denies. No shared owner token.  
G2: per-tenant membership, device/client channel and exact tool grants; A cannot call B tools; write/admin/Trader operations denied by default; Dhamma delegated per original authority.  
G3: authenticated real HTTP `initialize`, `tools/list`, `tools/call` for each approved read-only tool group, verified result from its real SSOT; negative test for unauthorized access.  
G4: quotas and concurrent calls test with strong D1 idempotency/atomic reservation, fraud/429 limits, timestamps and reconciliation; calls that failed or were blocked never billed; duplicates produce one usage event.  
G5: approved pricing (currency, rate, tax, plan, trial, refunds) -> test Stripe customer/subscription/meter mapping, verified signatures and replayed webhooks, correct entitlement updates, one invoice accounting reconciliation, no actual customer charge before owner gate.  
G6: independent verifier receipts, recovery/rollback after Ubuntu or edge failure, quota exhausted mode, audit without raw credentials or personal message content.

Source tests alone ≠ runtime acceptance. No price amounts, current subscriber count, enabled Stripe account or real user authorization are claimed.
