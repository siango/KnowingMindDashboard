# KBN Unified AI Execution: AV15 gap matrix and minimal patch plan

**Mission:** `KBN-UNIFIED-AI-EXECUTION`
**Owner:** AV15 / GitHub Copilot Cloud
**Assessment mode:** read-only inventory plus source-only reconciliation patch
**Assessment baseline:** live AV15 source checkout `0953492a690d6426dd8126d6e355e17f4053f16b` (dirty, retained); existing KMCW source PR #1 now at `5db015af35be57ac8db1862ffc746c68df11b33d` at recheck.
**Runtime policy:** expired; not extended. Production bindings, scheduler, queue, central state and trading were not changed.

## Executive result

The best-evidenced KMCW implementation remains the AV15 V2/V3/V3.1 filesystem runtime at `/mnt/c/AndroidProjects/KnowingMindSuite/av15/coding_worker_v2/`. It is a local JSON-file queue with terminal archives, leases, checkpoints and receipts. Its live checkout is dirty and behind the source PR; therefore the PR source must not be copied over it. The current PR source adds scrubbed subprocess environments, bounded file inventory/search, draft-only PR creation, valid inactive n8n definitions, honest blocked/partial plan status, bounded mission auditing, signed independent-verifier handoff and QC/receipt gates. These changes are present on PR #1 and were preserved.

Current PR #1 source gates terminal DONE on QC, authenticated independent verification, lease validity, auditor approval and a durable final receipt. The patch accompanying this matrix adds detection of terminal queue/receipt disagreement, blocks reuse on such conflicts and surfaces them as blocked monitoring evidence. Existing `g4reuse-t1` runtime evidence is untouched and remains unresolved/BLOCKED. This is detection/prevention, not automatic historical reconciliation.

No qualified central lease/fencing/readback authority is currently evidenced. A real central mission E2E and independent acceptance therefore remain blocked. This report and tests do not qualify the runtime for unattended/autonomous execution.

## Authority and capability inventory

| System / source | Authority role in existing design | Observed capability and permission | Gap / decision |
|---|---|---|---|
| AV15 KMCW V2/V3/V3.1 | Local execution state | JSON queue, local PID/expiry lease, checkpoints, local receipts; local read/write | Reuse as an execution adapter only. Local PID lease has no central signed fence/CAS. Runtime policy expired and autonomous execution stays disabled. |
| KMNW WN-00..18 approved design | Governance / intended network contract | Existing Worker Network design and implementation backlog, approved design document | Design is not runtime evidence. WN delivery states below must be qualified independently. |
| KCC / Cloudflare D1 | Intended central projection/queue in existing architecture | No qualified current D1 endpoint, binding, or authorized row readback established in this assessment | BLOCKED. Do not infer D1 from Cloudflare Worker presence or probe retired/forbidden endpoints. |
| Dedicated Dhamma MCP v4 / PostgreSQL | Central project truth and independent readback | MCP project status/readback calls returned internal error `-32603`; no authorized direct PostgreSQL readback established | BLOCKED. No substitute projection can claim canonical truth. |
| GitHub / Actions / source PR #1 | Source review, CI and evidence transport | Existing PR/branch, draft-only review flow, CI and repository evidence | READ/WRITE for source PR only. GitHub is not lease authority, SSOT, or production acceptance. AV15 runner status is not a lease. |
| n8n definitions / P02 scheduler | Orchestration under governed central authority | Six definitions in source; latest source PR repairs their JSON structure. Runtime n8n instance/qualified workflow was not observed; installed cron calls existing AV15 routine-only supervisor | Source definitions are not an active scheduler. Existing cron policy expired; no renewal or activation. |
| KMCW in-process tool registry / MCP | Local capabilities | Allowlisted in-process tools with read/write/destructive gates | This is not a connected MCP server. No MCPv4 adapter is evidenced in the KMCW runtime. |
| Admin V2 PR #275 | Operator projection | Existing 12-page navigation and Mission/QC/Fleet surfaces with fixture evidence | Source UI/fixtures do not create central truth. Runtime SSOT readback remains unverified. Binance page 11 remains read-only and unchanged. |

## KMNW WN-00 through WN-18 implementation matrix

Status labels describe evidence available in source/runtime review, not a claim of acceptance.

| Work node | Intended capability | Existing evidence | Current status / gap |
|---|---|---|---|
| WN-00 | Unified architecture and boundaries | Approved Worker Network design; existing unified execution fabric contract | DESIGN PRESENT; runtime authority qualification is missing. |
| WN-01 | Membership and earn-mode consent | Governance design only | GATED; no real members or reward work enabled. |
| WN-02 | Node identity, registration and revocation | KMNW prototype enrollment script and fixtures | PARTIAL / NOT VERIFIED; prototype has obsolete Muse/headless-source qualification dependency. |
| WN-03 | Android worker MVP | Mobile preflight implementation and sanitized fixtures | SOURCE ONLY; no physical device canary or independent runtime acceptance. |
| WN-04 | Windows worker MVP | AV15 KMCW V2/V3/V3.1 runtime and source | LOCAL EXECUTION EVIDENCED; dirty live checkout, local-only lease and expired policy prevent network qualification. |
| WN-05 | Heartbeat, capability and slots | Local lease heartbeat; historical fleet announcement | PARTIAL / STALE; no current global slot/heartbeat authority readback. |
| WN-06 | P02 n8n dispatcher/scheduler | Six importable workflow JSONs in source | SOURCE ONLY; source definitions repaired in PR #1; no active qualified n8n instance. Cron policy expired. |
| WN-07 | GitHub code review work-unit adapter | Existing Actions runner, source PR and review comments | PARTIAL; source review/CI works, but it is not a lease authority and does not independently verify runtime execution. |
| WN-08 | Sandbox and deterministic verification | Bounded sandbox implementation and tests in existing PR source | SOURCE / CI EVIDENCED; live AV15 runtime is behind newer source and has not been requalified. |
| WN-09 | Proof-of-Useful-Work evidence | Local `KM_CODING_RECEIPT_V1` files and QC evidence | LOCAL ONLY; lacks central signed fence/lease and independent central ACK. |
| WN-10 | Reward/pricing/energy ledger | Governance design | GATED; no production reward ledger or payout execution. |
| WN-11 | Payout/legal/accounting | Governance design | GATED; no payout/accounting acceptance. |
| WN-12 | Fraud/reputation/trust | Prototype/token and design artifacts | NOT QUALIFIED; no accepted network-wide trust/fraud authority. |
| WN-13 | Member/worker/reward API | Existing control/admin APIs | PARTIAL; not evidence of the complete qualified worker API or central acceptance path. |
| WN-14 | Cloudflare edge/access/D1 | Existing source contracts and Cloudflare project references | BLOCKED; no qualified live D1 binding/readback; retired dispatch endpoint is forbidden. |
| WN-15 | Worker/payer dashboards | Admin V2 existing PR #275 | SOURCE/fixture QC only; no central runtime truth readback. Preserve 12 pages; Binance remains original/read-only page. |
| WN-16 | Owned-device canary | AV15 local worker and Android preflight fixtures | PARTIAL; no independently accepted end-to-end device canary. |
| WN-17 | External beta | Governance design | GATED; no external beta or member/reward activation. |
| WN-18 | Scale/failover/regional/P2P | Architecture goals only | NOT IMPLEMENTED / NOT VERIFIED; do not create parallel schedulers, queues or workers to fill this gap. |

## Runtime evidence and boundaries

- Primary runtime absolute path: `/mnt/c/AndroidProjects/KnowingMindSuite/av15/coding_worker_v2/`.
- Local queue store: JSON files under `queue/{inbox,doing,done,failed,index,checkpoints}`; receipts under `receipts/`; not SQLite/PostgreSQL/D1.
- Lease: `queue/doing/<task>.lease.json`, PID plus acquired/heartbeat/expiry timestamps (300-second TTL); local heartbeat renewals only. No distributed fencing token or central CAS proof.
- Checkpoint: `queue/checkpoints/<task>.json`; receipts: `receipts/<task>.json`, schema `KM_CODING_RECEIPT_V1`; latest pointer in parent `av15/state/coding_worker_v2_last.json`.
- Runtime process: `coding_worker.sh --routine-only` invokes `python3 -m coding_worker_v2.run_once --routine-only`; wrapper and supervisor use local `flock` locks. No active KMCW process/held KMCW lock was observed in the recorded host inspection.
- Scheduler: existing cron invokes `av15/supervise.sh --routine-only` each minute. Policy `state/routine-policy.json` expired at `2026-11-02T18:45:11.422522+00:00`; it was not renewed. `paid_providers_enabled=false`, `central_mutation_enabled=false` in inspected policy.
- Existing services observed: GitHub Actions self-hosted runner and Ollama. This alone does not establish worker execution or central authority. No active n8n instance was evidenced.
- Conflict preserved: `g4reuse-t1` done archive says `DONE`; corresponding receipt says `FAILED` / `TEST_FAIL`, QC false. Prior recorded SHA-256: queue `4644d3c26f90ef5c5e53b98f383b3a97342ebe4af1ffc98eedc6197a21415b7c`, receipt `f637a1897a3d402f410a6cd805419e0957d091e0a14167f6684f59405668f868`. No original evidence was rewritten.
- Existing recovery probe is not disconnect acceptance: `ocr-v1-91-recovery-probe` reported deterministic self-test exit 0, but `waited_owner_session_absent=false`; `ocr-v1-90-after-disconnect` remained policy-denied/blocked. No real SSH/Codex disconnect acceptance is claimed.
- Local AV15 live checkout SHA `0953492a690d6426dd8126d6e355e17f4053f16b` is dirty and contains uncommitted changes; it has not been modified by this patch. The source PR head `e13b5d8d66ca2ed4ca2690ab999b531ab6210843` is the integration target and must not be blindly copied back to runtime.

## Existing envelope and adapter plan

Reuse the existing `KM_CODING_TASK_V1` task fields (`mission_id`, `task_id`, `owner_node`, `input`, `attempts`, `idempotency_key`) and preserve the existing unified execution fabric event contract. Map existing values to `event_id`, `mission_id`, `assignment_id`, `task_id`, `execution_key`, `correlation_id`, `causation_id`, `source_node`, `target_node`, `capability_id`, `event_type`, `state_before`, `state_after`, `attempt`, `lease_id`, `idempotency_key`, `evidence_ref`, `evidence_hash`, `observed_at`, `risk_class`, `approval_state`, and `contract_version` only when source evidence supplies them. Missing canonical assignment, lease/fence, authority ACK, or verifier attestation must remain `UNKNOWN`/`BLOCKED`; adapters must not synthesize those IDs or treat a GitHub receipt as a central lease.

Minimal safe progression:

1. Keep this patch local-source-only: detect conflicts read-only, block dedup/reuse, expose blocked monitor status; order QC → durable local receipt → terminal queue archive.
2. Preserve existing queue, scheduler, local receipt store, P02 announce/readback transport and D1/MCP/PG systems. Do not create a parallel queue, worker, scheduler, database, or SSOT.
3. Before any bridge write, obtain a qualified current authority endpoint and documented owner policy, then prove read-only central identity/readback plus lease/fencing/idempotency semantics through the approved MCP/authority path.
4. Only after that gate, build a small adapter around the existing task/event contracts. Local execution completion is `EXECUTION_DONE`, not `VERIFIED`; independent verification and central SSOT reconciliation are separate terminal gates.
5. Exercise duplicate, retry, timeout, disconnect, lease-expiry and recovery with synthetic temporary-store tests first. A central E2E requires an authorized non-production mission and independent readback; no such run is claimed here.

## Patch and verification record

- Final source delta over current PR head adds only `agent/memory.py`, `agent/monitor.py`, `tests/test_ssot_reconciliation.py` and this matrix. The earlier local commit `917ae4f…` also reordered executor receipt/QC writes, but that executor change is superseded by newer PR commits `c2ea9b0e` and `5db015af`; merge resolution retains the newer signed-verification implementation, which already gates DONE on QC and receipt persistence.
- New tests use temporary JSON stores and mocks; they do not read or write the live AV15 queue/receipt tree.
- The user's reported source result at `e13b5d8…` plus the initial patch was reproduced: **143 tests, 141 passed, 2 skipped**. The skips are the two tests below.
- Latest PR source at `5db015af…`: **173 tests, 171 passed, 2 skipped**. With the six reconciliation tests: **179 tests, 177 passed, 2 skipped, 0 failed** in `python3 -m unittest discover -s coding_worker_v2/tests -t . -v`.
- Latest-head skipped cases are exactly `test_live_plan_validates` and `test_live_coding_task_passes` in `test_agent_integration.py`; both call `require_live()` and skip because `KMW2_LIVE != 1`. They were not enabled because runtime policy is expired and no live acceptance authorization/runtime evidence is established.
- Latest source already tests receipt persistence/QC-before-completion, independent proof checks, duplicate intake/idempotency, stale fencing, queue recovery after lease expiry, heartbeat during slow inference and mocked Qwen E2E. These are synthetic/local tests, not an authorized central SSOT mission E2E.
- Source PR #1 existing CI at preceding head `e13b5d8…`: owner-reported CI runs `41342490583` and `41342497025` passed, plus `133 passed / 2 skipped` local at that older head. These are historical evidence and do not include this patch.
- No real AV15 mission E2E, central SSOT readback, live disconnect/timeout injection, independent QC acceptance, production acceptance, merge, or deployment is claimed.

## Blockers / handoff

1. **Central authority:** Dedicated Dhamma MCP returned `-32603`; direct authorized PostgreSQL readback was not available. Current qualified D1 endpoint/binding and signed lease/fence semantics remain unknown. A retired Cloudflare dispatch endpoint was explicitly forbidden for this product and must not be probed.
2. **Runtime rollout:** AV15 live source is dirty and older than the source PR; routine policy expired. Owner must review and separately authorize a controlled runtime rollout/policy decision after independent acceptance.
3. **Acceptance:** Independent QC and central readback are pending. Rewards, members, payouts, merge and deployment remain gated.

**Decision:** Source patch may be reviewed on existing KMCW PR #1 only. PR #275 is a different repository (`KnowingMindApp`) and its changed files are Admin V2/LINE Worker/auditor; it contains none of these KMCW modules. Do not mix this work into PR #275. Runtime integration and mission acceptance remain **BLOCKED / NOT VERIFIED**.
