# KMCW V3 scope reconciliation — AV15

This is source reconciliation, not FULL ACCEPTANCE or a production release.
The active AV15 checkout was based on `0953492a690d6426dd8126d6e355e17f4053f16b`.
PR #1 was based on `9446dbe097de8246c085730104333286c2f81537` and already contained
stronger mission-auditor, signed independent-verifier, lease-token and receipt
history gates. Those gates are retained. Older local queue/executor code is not
copied over them. No P02/PAD5 changes, deployment or new control plane is included.

## Requirement matrix

| Item | State | Actual evidence | Blocker / minimum next action |
|---|---|---|---|
| Runtime and Qwen | PASS on current AV15 runtime | Local `receipts/pkmcw-multi-qwen-local-t1.json`: one coding task, tests and QC passed, 218.6s CLI duration | This legacy-runtime receipt does not establish live acceptance of the newer signed-verifier gate version |
| Planner and intent | FAIL for complete source/runtime parity | Local plan `p20261105T194232` rev5 used Qwen schema output; rev6 used fallback after unknown tool rejection | Synced planner preserves read-only acceptance and grants no mutation paths; synced executor blocks read-only tasks with `READ_ONLY_TOOL_RUNTIME_NOT_RECONCILED` until inspection dispatch is ported through the existing gates |
| Live Progress | PASS for verified display behavior | Local `state/cli_progress_stderr.log`: 99.4s live plan invocation; source tests cover elapsed ticks, no fabricated percentage and TTY output | Synced observer preserves all admission/QC/verification gates; no live proof of a full signed-verifier execution is claimed |
| Codex CLI | UNVERIFIED for execution | Installed 0.166.0, authenticated; `pkmcw-multi-codex-cli-t1` failed availability with `CODEX_SUPERVISOR_ACTIVE_RECURSION_BLOCKED` | Run one approved bounded task outside a supervising Codex session; adapters are source-tested, automatic routing is held back |
| Copilot CLI | FAIL | `pkmcw-multi-copilot-cli-t1`: `COPILOT_PLATFORM_PACKAGE_MISSING`, startup exit 1 | Repair the existing platform installation/authentication before execution acceptance |
| Copilot Cloud | FAIL | Task `b6bf9539-1ca1-4232-9318-fc018be85052` completed, but returned a different head and created PR #4 despite `create_pull_request=false` and an existing head | KMCW rejected the session mismatch. PR #4 was closed without merge. Local new submissions are disabled; remote no-new-PR behavior must be independently resolved before any new dispatch |
| QC / receipt gates | PASS for the recorded tests and local readback | 137 source-candidate tests passed, including audited executor, QC, signed-verifier and receipt-reconciliation regressions; local Qwen receipt read back | No live independent-verifier proof was available; older runtime receipts are not promoted into that stronger acceptance level |
| KM Audit+ checklist | UNVERIFIED at runtime | Existing source `agent/auditor.py`, pinned 31-requirement contract and ADMIT/BEFORE_MUTATION/AFTER_TEST/BEFORE_CLOSE gates retained | `KMW2_MISSION_AUDITOR_ROOT` and independent-verifier public-key configuration were absent in this session; verify the existing deployment before claiming checklist completion |
| AV15 local SSOT | PASS for current local backend | Local `state/multi_ai/ssot_write_readback.json`; task `pkmcw-multi-qwen-local-t1` / DONE matched queue, receipt, idempotency index and latest pointer | Backend is existing AV15 filesystem QueueStore/JSON receipts; no central service is required or newly created |
| n8n / recovery / autonomous execution | UNVERIFIED | Six existing valid workflow definitions are inactive and have no deployed workflow IDs. Local 127.0.0.1:5678 and Windows gateway health/settings probes were unreachable | No configured reachable instance/credentials or workflow/execution ID; no replacement service, import or activation performed. Queue recovery tests do not establish n8n execution |
| Complete GitHub parity | FAIL; verified subset synchronized | This source commit on existing branch/PR #1 preserves the stronger remote gates | Legacy routing/cloud/queue wiring remains local and unpushed because it conflicts with the newer audit/verifier/lease APIs. No claim that every local development file is mirrored |

## AV15 SSOT ownership

- Owner: **AV15**.
- Backend: existing filesystem `QueueStore` and JSON receipt/history interfaces.
- Active queue: `/mnt/c/AndroidProjects/KnowingMindSuite/av15/coding_worker_v2/queue/`.
- Active receipts: `/mnt/c/AndroidProjects/KnowingMindSuite/av15/coding_worker_v2/receipts/`.
- Latest index: `/mnt/c/AndroidProjects/KnowingMindSuite/av15/state/coding_worker_v2_last.json`.
- Schemas: `KM_CODING_TASK_V1` and `KM_CODING_RECEIPT_V1`.
- `KMW2_STATE_DIR` only overrides the local pointer directory; it is not a new backend.
- The existing spend ledger was absent for the local-only acceptance and was not created or altered.
- GitHub is source control, not runtime SSOT. Optional heartbeat announcements are projections.
- P02/PAD5 are not required for local task execution, recovery or receipt readback and were not modified.

## Source changes intentionally held back

The original dirty AV15 worktree is retained. The following older-runtime wiring
is not substituted for the newer PR #1 implementation: local `agent/routing.py`,
`agent/cloud_provider.py`, `agent/tools.py`, `queue_store.py`, legacy parts of
`agent/executor.py`, `agent/qc.py`, `run_once.py`, and associated legacy runtime
integration tests and `MULTI_AI.md`. CLI adapters and progress rendering are
preserved in source with focused tests, but are not represented as automatic
Multi-AI routing acceptance. External provider flags fail closed in the synced
CLI until that routing is reconciled with the audited executor.

Runtime queues, receipts, checkpoints, live configuration, logs, temporary
acceptance fixtures, bytecode, credentials and unrelated unverified local work
are excluded from the commit. Their exclusion is intentional, not data loss.

## Verification

Source candidate: 137 tests passed, 7.773s. Live model calls were disabled for
that source-only regression run. Earlier AV15 local acceptance and its failures
remain separate evidence, not substitutes for source-version acceptance.
No merge, production deployment, new branch, new Issue or new PR is part of
this sync. The unintended service-created PR #4 is explicitly recorded above.
