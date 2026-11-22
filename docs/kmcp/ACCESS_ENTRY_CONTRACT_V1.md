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
