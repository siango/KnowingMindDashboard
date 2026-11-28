# KVES — Real Runtime Evidence & KARL Integration
Project: KEBUNATE Visual Engineering Studio (KVES)
Project key: KM-VISUAL-ENGINEERING-V1
Status: APPROVED DESIGN / IMPLEMENTATION PENDING
Mission parent: G0-G1 / KnowingMindDashboard PR #6
Date: 2026-11-24
Authority: reuse existing KMCP V5 / KMCW / KMCO / KMAA / KARL and canonical evidence ledger. No separate runtime, DB, queue or fake production claims.

## Decision
KVES is the visual node-level execution and evidence interface. Users may drag, connect, disconnect and validate existing MicroOps/tools, then run *authorized* graphs and see actual per-node execution state, input/output evidence, receipts and failure detail. KARL is the runtime diagnosis and repair feedback; KMAA V2 System Health is the fleet-wide view. Both use the same evidence identity and authorization.

## Canonical wiring
KVES Editor (within existing KM AI Admin V2)
→ graph validation against existing KMCO catalog and KMCP tool contracts
→ existing authenticated KMCP V5 dispatch and permission/authority gate
→ existing KMCW worker, queue, leases and self-hosted runner
→ existing receipts + SSOT ledger / GitHub Actions artifacts
→ authorized KMCP read-only evidence retrieval
→ KVES node overlays, route timeline and KMAA diagnostics.

## Visual node contract
For each node show operation_id/tool_id, origin node, input/output schema, permission class, source_sha, installed_sha, expected deployment SHA, run_id/task_id/circuit_id, lifecycle status, observed_at, evidence freshness, input/output digests, receipt URI/digest, independent QC and authoritative gate verdict. Sensitive input/output payloads remain protected; hashes/allowlisted summaries by default.
States: DESIGN_ONLY, READY_NOT_EXECUTED, QUEUED, RUNNING, COMPLETED_UNVERIFIED, VERIFIED, FAILED, DENIED, BLOCKED, STALE.
Visual presence, green design validation and returned tool JSON do not prove actual execution. Live status only when backed by current authenticated runtime evidence.

## Initial evidence collector
Reuse `scripts/host/karl-human-check.py` in PR #6. This is a read-only human-visible check that has been seen running on AV15 (2026-11-24 ~02:53 Thailand time) and reporting LOCAL_SOURCE, FETCHED_SOURCE, INSTALLED_MARKER and INSTALLED_SHA_MATCH=DIFFERENT. Those SHAs were not identical: local 6d9ebc6bfcf6, fetched df37c2fd7cee, installed 9f2a21a93483. The comparison to FETCH_HEAD is not authoritative proof of a wrong deployment. Replace the comparison with a pinned expected deployment SHA derived from real GitHub workflow/run plus receipt before granting any match verdict. Local MCP checks were still in progress in the screenshot; no end-to-end completion is claimed.

## Implementation slices
1. Evidence V1: extend existing KARL inspector with sanitized JSON (alongside human-readable terminal output), exact run SHA, per-check results and safe failure codes. Do not mutate runtime.
2. Read path V1: expose existing ledger/artifact evidence using already-authorized KMCP tools, with exact mission/task/run scopes, no new bypass endpoints.
3. KVES visual MVP: existing KMAA navigation + node canvas, drag/link/unlink, schema validation, evidence panel and timeline. Initially preview/read-only.
4. Native canary: run a bounded P01 read-only graph via existing owner authority, verify tools/list, inspect, denied unauthorized action, source/install SHA, receipt and independent QC.
5. Authorized execution: enable Run only for allowlisted operations after source/identity/permission/lease/receipt gates; preserve production approval separately.
6. KARL loop: on actual failure show root-cause/evidence/repair proposal, existing PR patch, CI and canary results; never silently auto-promote.

## Acceptance / stop conditions
- One live read-only graph returns a real receipt and per-node timeline whose source/run/task IDs match the authoritative worker.
- Unauthenticated submit and out-of-scope operations are denied and shown clearly in KVES.
- Stale or missing receipt is displayed as STALE/UNVERIFIED, not green or completed.
- Independent QC has its own evidence; deployment/prod gate remains NOT_GRANTED until explicitly verified.
- No duplicate queues, control plane, registry, DB, or paid LLM call; stop on quota, authority failure or unchanged blocker.

## Current evidence
- KARL policy registered at `docs/kmcp/KARL_AUTONOMOUS_REPAIR_LOOP_POLICY_V1.md`.
- Inspector source: `scripts/host/karl-human-check.py`.
- Existing KMCO route inventory: `av15/coding_worker_v2/agent/kmco_route_audit.py`.
- This file is a KVES project specification, **not** evidence of a working KVES editor, executed graph, or production E2E.
