# KM Coding Worker V2 (local-first)

This source checkpoint does not include AV15's receiver, heartbeat, or
reconcile implementation. Runtime compatibility and central receipt transport
remain unverified; this repository only writes local receipt files and a
latest-receipt pointer.

## Layout

- `config.py` — env config, paid-API gate (`paid_api_allowed`)
- `providers.py` — qwen-local, deepseek-local, deepseek-api, muse-cli, echo + `route_chain` / `generate_with_fallback` / `discover`
- `tasks.py` — envelope (Mission/Task/Owner/Input/Output/Status/Evidence/NextAction) + admission
- `pipeline.py` — inspect, bounded edit, patch, syntax, allowlisted tests, repair parsing
- `queue_store.py` — persistent queue, idempotency, lease + heartbeat, crash recovery
- `agent/planner.py` — plans carry per-task `allowed_paths`; V3 blocks edits
  unless the saved plan explicitly lists existing files for owner review
- `receipts.py` — durable local receipts + `av15/state/coding_worker_v2_last.json` pointer
- `run_once.py` — legacy queue/provider CLI; `--once` is blocked because it
  bypasses V3 audit and independent QC
- `tests/` — stdlib unittest suite incl. fake-Ollama e2e

## Run

```bash
cd av15
python3 -m unittest discover -s coding_worker_v2/tests -t .   # tests
python3 -m coding_worker_v2.run_once --health                  # queue health
python3 -m coding_worker_v2.run_once --discover                # provider availability
python3 -m coding_worker_v2.run_once --enqueue task.json       # admit a task
python3 -m coding_worker_v2.agent.km_main plan "task request"
python3 -m coding_worker_v2.agent.km_main run <plan>
```

V3 execution remains blocked unless all mandatory audit and QC gates,
including independent verification, pass. Each planned task must also list
its approved files in `allowed_paths`; missing or nonexistent paths block
execution before queue admission, and the editor cannot change other files.
`plan --allowed-path path/to/file` explicitly supplies the operator's approved
files (repeat the option for multiple files), including when model planning
falls back. It replaces model-selected paths for every task; it cannot grant
write scope to a read-only request. Review the saved plan before `run`.

When the AV15 runtime has local edits and the approved source is in an existing
separate checkout, configure `KMW2_SOURCE_ROOT` to that checkout's `av15`
directory and `KMW2_SOURCE_SHA` to its exact approved commit in the runtime's
`coding_worker_v2/.env`. The existing `km` launcher then imports that source,
rejects mismatched or dirty worker code, and retains the original AV15 queue,
receipts, plans and latest-pointer directory. Explicit `--base`, `--workspace`
and `KMW2_STATE_DIR` overrides remain available; do not point them at a new
store for acceptance. The source pin is printed on stderr, not inferred from
the target workspace's Git revision.
Task worktrees preserve the workspace's repository-relative subdirectory
(for example `av15/`); edit paths and tests remain relative to that workspace,
while the registry retains the checkout root for ownership and cleanup.

An admission-blocked run records the actual `admission_audit` in
`state/agent_last.json`. It creates no queue task or coding receipt and does
not establish coding E2E acceptance.

## Continuity, resource control and execution policy

`km providers` probes configured providers without inference and reports
quota source/freshness separately from availability. Undisclosed quota,
reset, tokens and billed cost remain UNKNOWN/null. Disclosed exhaustion
stops requests; a provider reset/Retry-After or bounded local backoff controls
retry. Exhaustion without a disclosed reset remains blocked, not guessed away.
The existing queue checkpoint `.provider-resources` holds these observations;
planner and executor share the same controller. Generation success is not a
verified task success.

Routing defaults to Qwen, then configured local/authorized alternatives,
ranked using reconciled receipt outcomes and measured latency/cost.
`run --provider ...` restricts the route. CLI subscription proposals require
`--allow-external --billing-mode existing-subscription` and permit at most one
dispatch per task. An interrupted/ambiguous dispatch is never blindly retried.
DeepSeek API remains independently configuration-, credential-, price- and
budget-gated; budget reservations survive unknown usage and interrupted calls.
Token-based price estimates are not reported as billed costs.
Muse is unavailable until a read-only proposal contract is verified.
Cloud task mutation remains gated separately from source-only PR handoff.

Use the same `run <plan>` after disconnect: a live duplicate is monitored,
while an expired lease/checkpoint resumes the same task and isolated worktree.
Checkpoints bind task input, source SHA and approved-file content digest.
Generated proposals/edits are not repeated; tests and mandatory QC are rerun.
Partial mutation or source/content mismatch fails closed. Provider cooldown
releases the lease and retains the task in the existing inbox. Pending human
approval cannot be automatically reclaimed.

Policy decisions record decision, rule ID, task ID, actor and evidence.
Scoped inspection, isolated approved edits, allowlisted tests and checkpoint/
receipt operations need no repeated internal prompt. Non-isolated edits,
workspace escapes, credentials/production paths, unknown commands, deployment,
merge/force push and independent signing require human approval. This policy
does not authorize host CLI prompts or independent QC signing.
`run --test-command` supplies an explicit allowlisted test command; worker
workspaces otherwise use the existing worker test suite.

`km status` reads existing checkpoints, receipts and audit events for task
phase, diff statistics, tests, provider/usage, retry/recovery, approvals and
next action. Four-part PROBLEM / CAUSE / SOLUTION_PATH / RESULT memory is a
derived view of original hashed receipts, not another database. Retrieval is
a hint, never proof: execution-only DONE is not VERIFIED_PASS, and changed
source cannot reuse old verification. Acceptance observations are displayed
only when their source SHA matches the executing worker.

## Environment (`coding_worker_v2/.env`, never committed with secrets)

| Var | Default | Meaning |
|---|---|---|
| `KMW2_OLLAMA_URL` | `http://127.0.0.1:11434` | loopback only, enforced |
| `KMW2_QWEN_MODEL` | auto | primary; must exist in `/api/tags` when set |
| `KMW2_QWEN_FALLBACK_MODEL` | unset | fallback when primary missing/failing |
| `KMW2_DEEPSEEK_LOCAL_MODEL` | unset (required) | explicit local DeepSeek name |
| `KMW2_DEEPSEEK_LOCAL_MIN_MEM_MB` | 6144 | hardware feasibility gate |
| `KMW2_DEEPSEEK_API_ENABLED` | 0 | must be `1` for paid calls |
| `KMW2_DEEPSEEK_API_BASE` | `https://api.deepseek.com/v1` | configurable adapter |
| `KMW2_DEEPSEEK_API_MODEL` | unset (required) | no assumed model name |
| `KMW2_DEEPSEEK_API_KEY` | unset | key, never logged |
| `KMW2_SPENDING_BUDGET_USD` | 0 | must be > 0 for paid calls |
| `KMW2_DEEPSEEK_PRICE_PER_1K_IN/OUT` | unset (required) | no assumed pricing |
| `KMW2_ALLOW_ECHO_FALLBACK` | 0 | deterministic offline tests only |
| `KMW2_CODEX_ENABLED` / `KMW2_COPILOT_ENABLED` | unset | configured CLI probes; execution still requires task authorization |
| `KMW2_MUSE_ENABLED` | unset | configured probe only; read-only contract remains unverified |
| `KMW2_MAX_REPAIR_ATTEMPTS` | 2 | bounded repair loop |
| `KMW2_TASK_TIMEOUT_S` / `KMW2_TEST_TIMEOUT_S` | 600 / 300 | resource limits |
| `KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY` | unset | PEM public key for external-verifier readback |
| `KMW2_VERIFIER_WAIT_S` | 30 | bounded external proof wait, 0–300 seconds; live lease required |

## SSOT / monitoring integration (no new control plane)

- Receipts land in `coding_worker_v2/receipts/<task>.json` with immutable local
  history under `receipts/history/`.
- Runtime SSOT owner is AV15: the local `coding_worker_v2/queue/` authority,
  JSON receipts/history and `av15/state/coding_worker_v2_last.json` index
  (`KMW2_STATE_DIR` can override the index directory). P02, PAD5 and GitHub
  are not runtime execution/recovery authorities.
- Health: `run_once --health` emits queue counts. Runtime heartbeat integration
  is not present in this source checkpoint.
- CI: `.github/workflows/coding_worker_v2.yml` (new file; `pages.yml` untouched).

## Independent verifier handoff

Before queue completion the worker writes a versioned request under
`state/verifier_handoff/` binding the task, plan/scope digests, source SHA,
patch digest, provisional receipt digest/version, and lease-token digest.
An independent verifier must return a sibling `.proof.json` signed with
OpenSSL SHA-256 using a private key unavailable to the worker. The worker
verifies it with `KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY`, checks distinct
verifier/runner identities and a 15-minute validity window, and reads back
the exact pinned artifacts. Missing or invalid proof leaves the task
uncompleted; this file handoff does not claim a live verifier transport.
Requests use `<task>.v<receipt-version>.request.json`; the verifier returns
`<task>.v<receipt-version>.proof.json` with `payload` and Base64 `signature`.
The verifier must publish the complete proof atomically (temporary file plus
rename). Only a missing file is retried during the bounded wait; invalid proofs
fail closed immediately. The worker never creates the proof or signing key.
The signature is over UTF-8 canonical JSON (`sort_keys`, compact separators)
of the payload, which must echo the request bindings and add PASS status,
distinct `verifier_id`/`runner_id`, evidence references, and `issued_at` /
`expires_at`.

## Engineering architecture checklist (Five-Khandha analogy)

This is an Owner-approved engineering analogy, not a claim about runtime
acceptance:

- RUPA — runtime and integration surfaces (AV15 components not source-verified)
- VINNANA — authenticated intake (n8n definitions are inactive; authentication
  is not configured)
- SANNA — deduplication, memory, SSOT, and receipts (local implementation;
  central transport unverified)
- SANKHARA — planning, execution, and routing
- VEDANA — risk assessment and QC evidence

## Acceptance evidence map

G1 source scope · G2 e2e vs fake Ollama (`test_e2e.py`) ·
G3 adapter tests + budget guard · G4 routing/fallback tests · G5 allowlisted
test-command sandbox · G6 e2e incl. repair · G7 lease-expiry recovery tests ·
G8 queue/lease/idempotency tests · G9 CI workflow · G10 receipt readback tests.
No benchmark harness/evidence is included in this source checkpoint. These are
repository-side tests; they do not constitute an AV15 runtime or production
acceptance.

## Agent-controllable command contract (source-only extension)

The existing `agent/tools.py:ToolRegistry` remains the **only** local tool
registry. `build_default_registry().command_contract()` exposes a stable,
SHA-256-fingerprinted declaration of each registered command's name, input
types, permission, side effects and limits. It is inert metadata: discovery
availability still comes from live probes; a source contract is **not** MCP
runtime evidence, authority to call a tool, or proof of acceptance.

This pattern adopts the *idea* of agent-controllable applications and parity
testing without importing or cloning ArtCraft code. No new MCP server, queue,
ledger, database, admin endpoint, worker or control plane is introduced.
The existing `call()` admission/permission checks remain authoritative.
Registration now rejects duplicate or ambiguous command signatures.
Per-call evidence records the outcome of the **current** invocation, not a
success flag left in context by a previous invocation; non-dict output is
a failure. `tests/test_agent_tools.py` pins deterministic contracts,
duplicate rejection, pass-then-fail replay, and invalid-result fail-closed
behavior. External independent verification and AV15 runtime acceptance
remain separate gates.
