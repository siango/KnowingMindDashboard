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


## 27 Nov 2026 — EP12 unified membership and login integration

Status: owner approved integration direction; source contract only, runtime implementation and independent acceptance pending. Continue existing EP12 Membership and Private Storage + KMCP V5 External Gateway; do not create a parallel identity database, mission, OAuth server or MCP runtime.

Canonical upstream membership design: https://github.com/siango/KnowingMindApp/blob/711c4bd642e19d2cef52fcf7bc1fa667c5bd0e91/docs/control/KNOWINGMIND_FULL_EPFM_P02_24X7_V1.md (EP12, MEMBERSHIP_V1). Reuse identity.users, identities, public_profiles and role_grants. Reconcile the existing D1 tenant/entitlement authority with this membership identity, not competing identity masters.

Observed owner screenshots at 19:30 ICT: knowingmind.app/login explicitly reports AUTH_BACKEND_NOT_WIRED; ai.knowingmind.app displays LINE login and a five-minute private-chat linking-code fallback. UI presence is not proof of provider verification or successful session. Existing Cloudflare bitter-king Access remains the transport boundary for protected Admin/MCP routes; ChatGPT is optional, never the system identity authority.

| Task | Owner node | Input | Required output / acceptance | Status |
| --- | --- | --- | --- | --- |
| P01 | Core/KMCW | EP12 and installed authority on AV15-SSD | Read back existing membership schema and identity mapping; reconcile D1 tenant policy; no new tables without evidence | Pending |
| P02 | KMNW/KMCW | Existing LINE login and linking flow | Verified provider subject, real session, expiry/logout, single-use linking code, cross-user denial | Pending |
| P03 | KMCW/Web | dhamma-web-channel-edge and knowingmind-line-webhook | Wire knowingmind.app/login and ai.knowingmind.app to shared identity; preserve anonymous public content; secure session exchange, no fake session | Pending |
| P04 | Core/KMCW | Existing provider adapters and kebunate-google-oauth-bridge candidate | Enable LINE, verified email/phone, Facebook, passkey, and Google only after provider/backend evidence; account linking requires reauthentication and verified provider identity, never auto-merge by email/name | Pending |
| P05 | KMCP/KMCO | Existing AccessGate, tenant-policy.mjs, registry | Verified issuer+subject mapped to same canonical user; per-tool least privilege; existing owner/service compatibility; non-ChatGPT external client acceptance | Pending |
| P06 | Core/KMAA | Existing entitlement, quota, ledger and Stripe integration | Verified subscription-to-entitlement mapping, idempotent usage, role/session administration; payment not identity proof or execution authority | Pending |
| P07 | Audit+ | Actual external and web runtime receipts | Two distinct users, cross-user isolation, revocation, logout/expiry, read-only MCP call, denial of unauthorized tools, ledger reconciliation, independent QC | Pending |

Web and MCP sessions are separate trust boundaries: share canonical identity, not raw tokens/cookies across services. Use verified provider assertions and least-privilege server-side exchanges, CSRF/state/nonce/PKCE where applicable; do not expose admin or private storage by default. No live trading permission follows membership. Preserve existing tunnel, port 18856, PostgreSQL, source registry and queue. Next action P01/P02 read-only delta inventory; stop at precise blocker, do not repeat prior checks without change. No deployment, login success or Core registration is asserted by this source update.


## 28 Nov 2026 — Local-first transport and KMAA X04 dependency
Owner-directed architecture: AV15 SSD co-resident KMCW/KMCO/Codex use existing local stdio/native registry with POSIX ownership and per-tool scoped authorization, not Cloudflare round trips. Private LAN access requires its own reviewed authenticated transport; do not expose loopback or bypass AccessGate. External clients (not ChatGPT-specific) use existing mcp.knowingmind.app/mcp, Managed OAuth or approved machine identity, verified signed Access assertion, canonical membership, exact grants, quota and usage ledger. Both transports reuse the same canonical registry, existing queue and receipts; no new server, registry, queue or database. Cloud-hosted KMAA cannot reach AV15 loopback; use approved external reader or existing evidence projection, with separate local/external freshness and acceptance. External failure must not block local-only work. Historical P02 references are not active routing instructions.
Acceptance: existing AV15 local initialize/tools/list/scoped read/denied write; actual external initialize/tools/list/approved read/unauthorized deny; benchmark same bounded workload with measured latency and correctness, source SHA and node; independent verifier receipt; KMAA 21 existing work packages and all 14 pages; single combined deployment after gate, then NakthamSuite 2563. No live trading or new grants. Preserve existing production and do not rerun historical job 137238103819. Status DESIGN_UPDATE / RUNTIME_ACCEPTANCE_PENDING.
