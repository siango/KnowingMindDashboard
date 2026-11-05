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
| `KMW2_MAX_REPAIR_ATTEMPTS` | 2 | bounded repair loop |
| `KMW2_TASK_TIMEOUT_S` / `KMW2_TEST_TIMEOUT_S` | 600 / 300 | resource limits |
| `KMW2_INDEPENDENT_VERIFIER_PUBLIC_KEY` | unset | PEM public key for external-verifier readback |

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
