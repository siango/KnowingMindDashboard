# KMCP V5 — One entry contract across versions (read-only-first)

**Status:** source contract only; NOT production cutover, NOT a second MCP server, NOT an identity grant.

## Why earlier versions repeated 401

The historical ingress conflated an application `Authorization: Bearer` credential with Cloudflare's *signed* `Cf-Access-Jwt-Assertion`. These belong to different authentication layers. A ChatGPT/OAuth bearer is not a Cloudflare Access JWT. The new ingress validates Cloudflare's signed assertion independently when present, and preserves legacy signed Access JWT-as-bearer only as a backwards-compatible fallback. Neither a caller-supplied email nor an unvalidated OAuth token constitutes Access identity.

## Canonical entry flow

1. **Transport gate:** existing Cloudflare Access application/policy and existing origin route, with a verifiable Access JWT (issuer, audience, signature, expiry, subject). Missing/invalid JWT => 401, including when an OAuth bearer happens to be present.
2. **Application client identity:** ChatGPT interactive OAuth or approved service-to-service credentials. This is independent of the Access JWT, and each deployment must verify its actual supported authentication mode. Cloudflare's service-token pair is for service-to-service use, not evidence of a ChatGPT OAuth user or an Admin grant.
3. **Per-tool authorization:** the existing KM AI Admin session/CSRF/context producer and exact tool bindings. Valid transport identity alone never grants `tools/call`, database writes, command execution or Trader live orders.
4. **Existing execution:** installed KMCP Registry -> KMCW existing queue and receipts, Dhamma delegated to its authoritative Dedicated Dhamma MCP/PostgreSQL. Do not merge Dhamma and KEBUNATE credentials or SSOT.
5. **Independent evidence:** exact active source SHA, Cloudflare Access/HTTP origin probe, authenticated `initialize`, `tools/list`, one approved read-only `tools/call`, denied write negative case, upstream authority readback, independent verifier, rollback receipt.

## Operator acceptance matrix

| Check | Required result | Insufficient proxy |
| --- | --- | --- |
| Original HTTP service | active, exact source SHA and bind paths readback | CI success |
| Anonymous / invalid auth | 401, no tool dispatch | 200 /health |
| Cloudflare signed assertion + distinct OAuth bearer | transport allowed only for verified assertion | string equality between headers |
| OAuth-only without Access assertion | fail closed unless separately authorized/validated by a reviewed entry policy | blindly trusting bearer |
| Installed Registry | actual list reconciles to expected tools | source tool count |
| Production origin | authenticated real-origin MCP initialize + tools/list | isolated loopback fixture |
| Tool read | exact scoped trusted context + actual readback | transport permission alone |
| Tool writes | explicit approved risk/authority gate, negative controls | presence of any token |
| Restore and independent QC | actual verified receipt and restored readback | artifact upload success |

## Known live blockers, 23 Nov 2026

- ChatGPT KnowingMindDhamma connector still returns HTTP 401 on project/status/health/readback.
- Existing `kmcp-v5.service` readback source exposes `kmcw_readiness` only, not the entire installed registry.
- Current Ubuntu origin has no observed `CREDENTIALS_DIRECTORY`, Admin session credential, KMCW path/catalog env bindings, or historic DHAMMA Access env file; the latter was previously delivered on an earlier AV15 environment only.
- Cloudflare Access application configuration and ChatGPT connector reauthorization cannot be inferred from repository tests.
- `pg_dump` separately fails `PERMISSION_DENIED`; this is not an Access/HTTP token issue.

**Never fix 401 by disabling Access, using a static global shared admin bearer as per-user OAuth, committing secrets, copying Dhamma secrets into KMCW, creating a duplicate worker/queue, or claiming production acceptance without actual readback.**


## 27 Nov 2026 — External gateway reconciliation (KMCP V5, multi-client)

Status: owner-directed design update; implementation and live acceptance PENDING. This supersedes the historical 23 Nov blocker snapshot only where new evidence is recorded, not the existing security rules.

Reported AV15-SSD evidence: existing kmcp-v5.service, loopback 18856 and Cloudflare tunnel route to mcp.knowingmind.app; external unauthenticated MCP request returns 401 with protected-resource metadata; team issuer is https://bitter-king-9b22.cloudflareaccess.com. Managed OAuth was enabled in the existing application. Subsequent interactive connection reports "Redirect URI not allowed by application configuration". This is an authorization-server rejection before origin dispatch; it is not evidence of database failure or invalid existing service credentials. Exact requested callback and successful token exchange are unverified.

### Required delta, in order

1. Reconcile existing Access application, policy, advertised resource/issuer/registration and actual client registration. Capture only decoded redirect_uri (never state, code or token). Allow exact callbacks for authorized clients only; no ChatGPT-specific global requirement, no broad wildcard, no new authorization server or public bypass. Preserve approved service auth and owner policy. Human web clients, external AI, and machine clients have distinct authentication contracts. Discovery must describe the actual public MCP resource and supported authorization flow.
2. At the existing origin, verify the actual managed OAuth credential/assertion delivery against Cloudflare's contract; do not assume an OAuth bearer is an Access JWT. Preserve signature, issuer, audience, expiry, token type, subject and duplicate-header denial. Keep fixed owner subject as fail-closed staging baseline until authoritative membership resolution is implemented and tested. Never simply remove the subject check.
3. Reuse cloudflare/kmcp-edge/tenant-policy.mjs and existing D1 authority for issuer+subject membership, tenant/channel, exact tool grants, quota and idempotent usage. The policy is source-only; current channel enum is CHATGPT/WEB/CLI/SSH/LINE/FB/AGENT. Extend reviewed channel mapping for other clients only when required; derive it from trusted registration/context, not caller claims. Enforce access across all paths, including /flow; do not trust client-supplied identity. Separate authentication failures (401) from authorized identity with denied permission (403 or appropriate MCP error). No global shared admin credential.
4. Reconcile actual installed SDK/transport and negotiated MCP protocol; keep existing loopback bind and registry. The branch's av15/mcpv5_secure_http_readback.py exposes only kmcw_readiness and explicitly disables execution: this is not proof of the live full registry. Locate the installed canonical registry and reuse it; no duplicate dispatcher. Filter tool discovery and enforce grants at invocation; read-only first. Dhamma remains separately authoritative.
5. Acceptance: existing owner and machine client compatibility; OAuth discovery and actual callback/token exchange for a supported interactive client; second distinct authorized principal including a non-ChatGPT client; unknown/revoked identity denied; cross-tenant/tool write denied; actual external initialize/tools/list/read-only tools/call with receipt; quota and ledger verification against existing authority; independent QC and rollback. CI is not live acceptance. Stop on a precise failing gate; do not rerun historical jobs or alter database, tunnel, port, secrets or trading authority to repair OAuth.

Owner nodes: KMCP/Cloudflare (step 1); KMCW/KMCP (2,4); Core/KMCO (3); Audit+ (5). Next action: reconcile actual redirect and credential delivery before changing production origin. No merge, deployment, production access grant or execution authority implied by this update.
