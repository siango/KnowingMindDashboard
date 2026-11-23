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
