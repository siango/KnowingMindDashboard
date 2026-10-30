# KM Coding Worker V2 (local-first)

Extends AV15 without modifying it. Existing `receiver.py`, `worker.sh`,
`supervise.sh`, heartbeat and reconcile files are byte-identical to Phase 0.

## Layout

- `config.py` — env config, paid-API gate (`paid_api_allowed`)
- `providers.py` — qwen-local, deepseek-local, deepseek-api, muse-cli, echo + `route_chain` / `generate_with_fallback` / `discover`
- `tasks.py` — envelope (Mission/Task/Owner/Input/Output/Status/Evidence/NextAction) + admission
- `pipeline.py` — inspect, bounded edit, patch, syntax, allowlisted tests, repair parsing
- `queue_store.py` — persistent queue, idempotency, lease + heartbeat, crash recovery
- `receipts.py` — durable local receipts + `av15/state/coding_worker_v2_last.json` pointer
- `run_once.py` — CLI: `--once`, `--enqueue`, `--health`, `--discover`
- `benchmark.py` — reproducible A/B/C/D harness, honest SKIPPED rows
- `tests/` — 39+ stdlib unittest tests incl. fake-Ollama e2e

## Run

```bash
cd av15
python3 -m unittest discover -s coding_worker_v2/tests -t .   # tests
python3 -m coding_worker_v2.run_once --health                  # queue health
python3 -m coding_worker_v2.run_once --discover                # provider availability
python3 -m coding_worker_v2.run_once --enqueue task.json       # admit a task
./coding_worker.sh                                             # recover + run one task
```

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

## SSOT / monitoring integration (no new control plane)

- Receipts land in `coding_worker_v2/receipts/<task>.json` (durable offline).
- Latest pointer `av15/state/coding_worker_v2_last.json` is picked up by the
  existing `heartbeat/reconcile.sh` + P02 bridge announce flow.
- Health: `run_once --health` emits queue counts; heartbeats stay in `collect.py`.
- CI: `.github/workflows/coding_worker_v2.yml` (new file; `pages.yml` untouched).

## Acceptance evidence map

G1 existing files untouched · G2 e2e vs fake Ollama (`test_e2e.py`) ·
G3 adapter tests + budget guard · G4 routing/fallback tests · G5 allowlisted
test-command sandbox · G6 e2e incl. repair · G7 lease-expiry recovery tests ·
G8 queue/lease/idempotency tests · G9 CI workflow · G10 receipt readback tests ·
G11 `benchmarks/evidence_*.json` · G12 `coding_worker.sh` restart-safe run.
