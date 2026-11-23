# KARL — KEBUNATE Autonomous Repair Loop
Status: ADOPTED DEVELOPMENT AGREEMENT / AUTOMATION NOT YET VERIFIED
Mission: G0-G1 and all KEBUNATE/KnowingMind subprojects
Version: KARL_POLICY_V1
Adopted: 2026-11-24
Owner: KEBUNATE project owner
Canonical source authority: existing repository source + SHA + runtime receipts; this document defines operating policy, not proof of a running autonomous repair service.

## Purpose
Convert an observed failure into a bounded, evidence-based repair cycle without repeated manual terminal archaeology, duplicate infrastructure, uncontrolled paid-model usage, or false claims of completion.

## Operating contract
1. One existing mission, task ID, repository, branch, source SHA, and acceptance contract. Resume prior work instead of creating a parallel mission, runtime, queue, registry, or control plane.
2. Capture one sanitized failure bundle (runtime error class, relevant stack/log excerpts, runner/installed/source SHA, CI jobs, existing evidence/receipt IDs, environment *names* only; never secrets or raw credentials). Prefer GitHub Actions artifacts and direct connector reads over screenshots or repeated manual commands.
3. Establish REALITY / ASSUMPTION / UNKNOWN. Never label source code, a designed MicroOp, unit-test PASS, or returned tool payload as production/E2E success.
4. Diagnose related root causes across source, workflow triggers, dependencies, authority, runtime bindings, and negative tests before patching. Review the full affected dependency slice, not only the first error.
5. Prepare a consolidated minimal patch on the existing branch/PR with regression tests; preserve valid behavior and negative authorization gates. Pin exact SHA; do not reset owner modifications or reuse stale receipts.
6. Execute existing GitHub-hosted CI where triggered, then existing self-hosted ubuntu-usb nonprod acceptance only when workflow policy and runner labels match. Reuse workflows; avoid repeated reruns, new infrastructure, and unbounded Codex/LLM calls.
7. Every run returns machine-readable evidence: mission/task, base/head SHA, installed SHA, workflow/run/job IDs, input reference, output summary, receipt hash, accepted checks, denied checks, independent QC, authority decision, blocker and next action. Redact sensitive data.
8. Promote only with authentic scoped authorization, validated non-stale authority snapshot, readback, independent QC, rollback evidence, and explicit production gate. Never forge receipts/snapshots, bypass auth, or silently deploy financial/payment/identity actions.
9. Stop conditions: acceptance met with verified receipts; unchanged failure and no new evidence; missing authority; exhausted budget/quota; unsafe or unbounded side effects; failed provenance/source SHA alignment. Escalate a concrete blocker to owner instead of looping.
10. Each response/report must include Current Context, Mission, Task Breakdown (owner, input, output, status, evidence), Execution Status, Blocker, Next Command. Keep statuses VERIFIED / PENDING / BLOCKED / ASSUMPTION explicit.

## Suggested execution pipeline (future automation)
OBSERVE → DEDUP → SCOPE/SSOT → COLLECT EVIDENCE → RCA → PATCH EXISTING PR → CI → SELF-HOSTED CANARY → VERIFY RECEIPT → INDEPENDENT QC → AUTHORITY GATE → CLOSE OR ESCALATE.
Use deterministic collectors, comparisons, policy and test runners without LLM where possible. Invoke AI only for bounded source-repair proposals, subject to quota and tool authorization. AI-generated changes remain PR-gated and test-gated, not implicitly trusted.

## Automation stages
- Stage 0 (now): policy in GitHub; human or orchestrator starts repair; GitHub Actions may already run on existing triggers. No claim of autonomous detection or repair.
- Stage 1: wire existing GitHub issue/check-run/artifact evidence into one read-only, event-driven diagnostic packet with dedup and budget limits.
- Stage 2: authorized bot proposes minimal patches to existing PR with automated unit/regression tests; no production permission.
- Stage 3: automatic nonprod self-hosted canary with signed/digested receipts, independent QC, and owner exception gate.
- Stage 4: constrained self-heal for explicit low-risk allowlisted operations only, with rollback, observability, and authority; high-impact actions always require their applicable approval.

## Acceptance for claiming KARL operational
An actual failure is detected; a bounded evidence packet identifies one root cause; a single minimal patch is committed on the existing PR; CI passes on its exact SHA; a self-hosted job executes on the intended node; installed/source SHA match; unauthorized calls are denied; a real execution receipt plus independent QC is returned; no unauthorized production change occurs; no duplicate repair loop is triggered. Until then: POLICY_ADOPTED, AUTOMATION_PENDING.

## Boundaries
GitHub source and CI are not the same as runtime authority. GitHub-hosted Actions do not prove self-hosted runner online, installed package alignment, HTTP authentication or production acceptance. The Existing Authority Gate remains authoritative. Never upload credentials or private customer data in failure artifacts.

## KVES real-execution visual interface (approved extension, implementation pending)
KVES (KEBUNATE Visual Engineering Studio, KM-VISUAL-ENGINEERING-V1) is the primary human-facing visual graph for KARL. Embed it in the existing KM AI Admin V2 rather than introducing another dashboard, queue, registry or execution authority.

**Graph behavior:** drag/drop/attach/detach existing KMCO MicroOps and KMCP tools; show input/output port schemas and required permission for each node; draft and validate bounded graph locally. A visual connection or green design check never implies an actual runtime handler or a completed operation.

**Run behavior:** clicking Run submits the version-pinned graph only through existing authenticated KMCP → KMCW dispatcher / original queue and authority gate, subject to tool permissions, idempotency key, concurrency/lease and quota. Never allow untrusted UI fields to assert verified identity, approval, scope or execution authority. Default preview and read-only evidence checks; mutations and production promotion require their own explicit approval.

**Live evidence:** subscribe/poll the existing authorized event/receipt interfaces for exact mission_id, task_id, graph_version, source_sha, installed_sha, circuit_id, node_id and tool_id. Render each node's actual state (DESIGN_ONLY, READY_NOT_EXECUTED, QUEUED, RUNNING, COMPLETED_UNVERIFIED, VERIFIED, FAILED, DENIED, BLOCKED), start/end time, error class, input/output digest, receipt link, independent QC and owner authority. Distinguish latest observed timestamp from freshness/expiry. Never simulate live output or claim REAL_TIME when only historical data is available.

**KARL inspector adapter:** reuse `scripts/host/karl-human-check.py` on AV15 as a read-only evidence collector, not as the execution engine. Evolve it to emit a privacy-safe versioned JSON report plus optional terminal human-readable output, with exact expected deployment SHA from authoritative workflow/run metadata, not FETCH_HEAD. Send reports through the current GitHub artifact/SSOT ledger and existing KMCP authorized read endpoints; do not upload secrets or raw user content.

**Visual result path:** KVES editor → existing graph validator → authorized dispatcher → existing worker/runner → execution receipt and trusted ledger → KMCP read endpoint → KVES node/edge overlays and event timeline. KMAA System Health/Diagnostics displays fleet aggregate; KVES presents the same evidence at each node and route (R01-R13). Both share receipt IDs, never maintain competing truth.

**Acceptance:** one bounded P01 read-only graph through real local MCP / existing worker reaches a terminal state with valid receipt and source SHA alignment; negative unauthorized submit is denied; UI shows actual receipt and evidence freshness, independent QC remains NOT_RUN until separately verified; disconnect/stale event is labeled UNVERIFIED. Automated self-repair remains separate, policy and gate-controlled. No claim of delivered KVES UI, real-time streaming or E2E until runtime and browser evidence exist.

## Single evidence authority / no repeat verification (G0-G1 owner directive)
Each actual CI, runner, local MCP, native circuit, and QC execution must record evidence **once** with a shared correlation key: `mission_id + task_id + source_sha + installed_sha + scope_digest + check_id + run_id`. Use the existing KMCW receipt history and its latest pointer for task/runtime receipts, plus the existing canonical evidence ledger for cross-node observations. GitHub Actions artifacts are immutable readback/projections linked by run/job ID, **not** an independent competing SSOT. Human-readable Inspector output and KVES/KMAA are views of those receipts only.

Before a new check, query existing scoped evidence and its digest, time, trusted source and acceptance contract. If a valid matching record is fresh enough and the source/installed SHA, identity, permissions, dependency graph and required authority have not changed, **REUSE VERIFIED** and do not rerun. If evidence is absent, stale, incompatible, rejected, or a live/production check expressly requires freshness, run only that missing or invalid check and append a new versioned receipt; never overwrite prior results or synthesize a PASS from a screenshot. Different SHA or changed contract means a new verification obligation, not an automatic failure.

The 2026-11-24 AV15 Inspector showed `MCP_INITIALIZE=PASS`, `MCP_TOOLS_LIST=PASS count=101`, and `KMCO_INSPECT_P01=PASS`; this is human-visible evidence from an attached terminal screenshot, **not yet a canonical machine-readable stored receipt**. Do not claim the shared ledger was updated by that screenshot. For the next actual runner check, publish a sanitized machine-readable receipt through the existing receipt/artifact path with exact SHA/run correlation. Never re-run these read-only probes just to reproduce screenshots if source+installed+scope remain identical and a trusted matching receipt already exists.

Gate table: `PASS+trusted receipt+matching provenance+valid freshness => REUSE`; `PASS only in terminal => OBSERVED_UNPERSISTED`; `NOT_TESTED/STALE/SHA_MISMATCH => PENDING_SCOPED_CHECK`; `DENIED => preserve denial evidence`; `AUTHORITY_NOT_GRANTED => STOP`. Neither KARL nor a UI may waive independent QC or production authority.

## KEBUNATE Core: รู้จำทำ + อริยสัจสี่ as the event-to-learning authority
Canonical owner directive: `siango/KnowingMindApp/docs/KEBUNATE_CORE.md`. This supersedes any interpretation of KARL, GitHub Actions artifact indexes, or KMCW task receipts as a *separate* central learning database.

Map all events in the same ongoing mission to **รู้** (capture original incident, context, timestamps, input/output references and provenance), **จำ** (link versioned receipts to existing problem/cause/solution/result memory and reusable prior lessons), and **ทำ** (decide REUSE / REVERIFY / REPAIR / BLOCKED through existing KMCW/authority, execute, verify actual outcome and append a lesson).

Use the four-part engineering mapping already defined by KEBUNATE Core: ทุกข์ = observed problem and impact; สมุทัย = evidenced cause distinct from assumption; นิโรธ = measurable acceptance/verified outcome; มรรค = authorized repair path and regression prevention. Never infer an outcome merely from a test count or an artifact filename.

Reuse existing structures, not parallel stores:
- KMCW local versioned task receipts: `av15/coding_worker_v2/receipts.py`; dedup/context decisions: `agent/memory.py`
- Existing Evidence Ledger/mission audit and GitHub artifacts: supporting observations and transport, never independent promotion of truth
- `siango/KnowingMindApp/docs/memory/DHAMMA_ARIYASACCA_WORK_MEMORY_V1.md` defines PROBLEM / CAUSE / SOLUTION_PATH / RESULT with original-event provenance and dedicated MCP controlled write/readback
- Existing `schemas/memory/003_memory_work_cases_v1.sql` is expressly **project-scoped to DHAMMA_GROUNDED_AI**, and its source documentation states production deployment/write/readback was pending at baseline. Do NOT insert G0-G1 KEBUNATE events into that schema, loosen its project constraint or claim it is the fleet-wide live database without explicit authority and schema readback

For G0-G1, first verify the existing authorized central registry/evidence store and applicable work-case bridge; if a KEBUNATE-wide event-recording capability is absent, record a specific scoped blocker and route integration through existing services, not a new DB. Every actual check/repair should preserve one event chain: mission/task/incident fingerprint → observation receipt/source SHA → verified cause → bounded repair commit → CI/runner run and receipt → independent QC → close or next blocker. Read back the stored event through authorized MCP before claiming durable capture or cross-session retrieval. No duplicate tests solely for memory recording.

Current status: **DESIGN LINKED; CENTRAL AUTOMATIC EVENT WRITE AND MCP READBACK NOT VERIFIED**. GitHub-only `karl-evidence-index.json` is a temporary evidence projection, not the Ariyasacca/รู้จำทำ authoritative memory.
