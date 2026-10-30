# Phase 0 — Reuse/Gap Report: V3 + V3.1 (verified 2026-10-30)

Parent: KM-CODING-WORKER-V2-AV15. Mode: reuse-extend-verify. No new control plane.

## 1. Verified reusable (do not recreate)

| Component | Location | Reuse as |
|---|---|---|
| Provider abstraction + Qwen primary/fallback + budget guard | `av15/coding_worker_v2/providers.py`, `config.py` | Model routing, local fallback, spend guard |
| Task envelope + admission | `tasks.py` | Task intake validation |
| Pipeline: inspect/edit/patch/syntax/allowlisted tests/repair parse | `pipeline.py` | Execution + sandbox base |
| Queue + idempotency + lease/heartbeat + recovery | `queue_store.py` | Task authority (SSOT for queue state) |
| Receipts + `coding_worker_v2_last.json` pointer | `receipts.py` | SSOT evidence leg |
| Orchestrator `run_task` | `run_once.py` | Single-task execute loop (extend with chain/history injection) |
| Benchmark harness | `benchmark.py` | Variant comparison |
| CI workflow | `.github/workflows/coding_worker_v2.yml` | G12 gate (auto-discovers new tests) |
| P02 announce + #118 GraphQL readback | `heartbeat/reconcile.sh`, hub `p02_execution_bridge_v1.py` | SSOT announce/readback pattern |
| Heartbeat tick + ring | `heartbeat/collect.py` | Monitoring base |
| Adapter contract doctrine (fail-closed, idempotency, evidence) | hub `adapter_contracts.json` | Tool router policy |
| `gh` auth (siango, repo+workflow scopes) | host | Commit/PR/CI integration |

Live runtime verified: Ollama `http://127.0.0.1:11434` (host net) serves
`qwen2.5-coder:1.5b` + `qwen2.5-coder:0.5b`; 1.5B inference OK (~27s cold,
~0.1s eval). Suite: 46/46 unittest green.

## 2. Gaps (V3 must add, V2 must not be rebuilt)

- P1 planner: no NL intake, decomposition, deps, plan schema, revision.
- P2 memory: no fingerprints, no reuse/resume/reverify classification.
- P3/P5 MCP router: no in-repo tool registry; Dhamma MCP is harness-side.
  Local allowlisted registry wrapping existing functions is missing.
- P4 executor: no plan-level loop (only single-task `run_task`), no backoff.
- P5 QC: checks exist as fragments (syntax/tests/CI); no supervisor,
  no risk-based selection, no bounded repair orchestration.
- P6 monitor: no unified MISSION/TASK/QC/NEXT_ACTION view.
- V3.1 worktree: `git worktree` unused; no registry/isolation/cleanup.
- V3.1 sandbox: allowlist+timeout exist; missing env secret isolation,
  formal verify report.
- V3.1 n8n: binary present (`n8n 1.109.1`, Win path), no instance data in
  WSL (`~/.config/n8n` absent), no workflows. Ship importable workflow
  JSONs; do not claim a running instance.
- V3.1 multi-AI: `codex-cli 0.165.0` works; `@github/copilot` broken in
  WSL (no platform package); DeepSeek adapters exist but unconfigured;
  Muse present but must stay on-demand only after handoff.
- Serena: absent (no `.serena`, no config). No integration possible;
  code search stays local (rg/python).
- KM AI Admin page: only vendored hub copy under ignored `av15/state/`;
  no committed source. V3.1 e2e targets the closest committed admin
  surface instead (evidence will name it).
- KM Coding CLI (REPL): planned under CLI-V1, never implemented (0 files).
  V3 absorbs it: `km` becomes the agent entry (subcommands), no REPL.

## 3. Do-not-touch

`receiver.py`, `worker.sh`, `supervise.sh`, heartbeat/reconcile, `pages.yml`,
nested `KnowingMindDashboard` repo, siang pm2 state, docs badge blocks,
vendored hub content, `kebunate_ceo_mission_002`. No paid APIs. No new
scheduler/queue/SSOT. No Muse changes before acceptance.
