# KEBUNATE MCP V5 — ChatGPT Direct Execution Channel

Status: DESIGN / NOT DEPLOYED / NOT AUTHORIZED
Mission: G0-G1-MINIMUM-SELLABLE-DEMO
Owner node: AV15 (Ubuntu Desktop USB, same physical machine)
SSOT: Existing KnowingMind governance and evidence ledger
Scope: Reuse KMCW V3, KMCO 132 design catalog, ToolRegistry, QueueStore, verifier. No second coding worker, registry, or public write gateway.

## Intent
Enable a trusted ChatGPT/approved MCP client to submit a bounded coding task, observe progress, request a test, read QC and receipts, and evaluate completion without manual command copy/paste. The connection itself does not confer authority.

## Architecture
Client -> OAuth-authenticated HTTPS MCP Streamable HTTP endpoint -> existing Cloudflare edge/tunnel policy -> local authenticated gateway -> KMCW V3 authenticated tool contracts / queue / receipt readback. Keep local subprocess MCP stdio bound to localhost; do not expose stdio or a naked HTTP-to-shell bridge.

Do not assume existing mcp.knowingmind.app has a working route or a permitted identity. Verify DNS, tunnel ingress, cert, and owner authority before touching production. No P02 fallback.

## Required tool capabilities (logical, not fabricated grants)
1. km.mission.read — read canonical mission and SHA
2. km.task.submit — submit to existing KMCW queue with owner-approved scope, idempotency and account/mission/task identity
3. km.task.status — read existing task and progress
4. km.task.test — request bounded test via existing runner; no arbitrary shell
5. km.task.qc — read independent QC1–QC7 evidence (no self-grading)
6. km.task.receipt — read immutable receipt, SHA and verifier result
7. km.task.resume — existing checkpoint and retry policy, only when explicitly authorized

These are capability targets; DO NOT register empty or fake handlers. Prefer already existing KMCW tool names where possible.

## Security gates
- OAuth 2.1 or equivalent approved end-user auth; HTTPS, explicit client identity and narrow scopes.
- Preserve Admin V2 signed delegation and existing KMCW qualified/authorized()/approval checks; bridge authentication is not a bypass.
- No credentials, approval objects or private keys returned to client. Never generate or forge verifier proofs.
- Limit queue size, test command allowlist, repository paths, runtime duration and daily budgets.
- Every mutation requires task-bound approval and workspace SHA; no general shell, deploy, file deletion or unrestricted git push.
- MCP tools/list is discovery, never proof of callable authority.
- Independent verifier and append-only receipt readback required before SUCCESS.
- TLS, Host/Origin validation, request body limits, timeouts, audit, retry idempotency and replay protection.
- Production endpoint remains closed until acceptance, owner approval, and published rollback.

## Acceptance / Evidence
A. Local existing MCP stdio initialize/tools/list PASS (historically 70 tools / 3 KMCO gateways).
B. Authenticated remote initialize/tools/list with correct owner and scopes.
C. Unauthenticated calls DENY; mismatched account/mission/task/path/SHA/expired scope DENY.
D. Submit exactly one existing canonical pilot task through KMCW; task ID returned by existing queue.
E. Observe real progress and bounded test output; source SHA and test exit status recorded.
F. Independent QC executed by separate verifier; signed proof and immutable receipt readback.
G. Reconnect after network loss, dedup same task, no duplicate actions.
H. Cloudflare route and reboot persistence proven, with production-safe rollback.
Report independently: discoverable, callable_authorized, executed, QC_verified, production_ready.
Do not claim runtime-ready or 132/132 executable merely because catalog loads.

## Known source state (2026-11-21 screenshots)
KMCO 132 design entries / 24 groups, 3 local gateway tools, 70 tool discovery, P01 5/5 local execution, local acceptance pass. Remote MCP, authenticated execution, independent QC, signed receipt remain NOT_RUN/BLOCKED.
