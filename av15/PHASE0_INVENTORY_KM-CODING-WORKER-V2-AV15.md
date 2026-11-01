# PHASE 0 — Verified Inventory and Gap Report
MISSION: KM-CODING-WORKER-V2-AV15 (verified 2026-10-30, worker workflow + direct re-check)

## 1. Repository and Git (verified by re-run)
- Root: /mnt/c/AndroidProjects/KnowingMindSuite, branch main, HEAD 0131a9f1, up to date with origin/main.
- Remote: https://github.com/siango/KnowingMindDashboard.git.
- Worktrees: single entry (repo root). No unknown worktrees.
- Uncommitted: 9 modified unstaged, 0 staged, 4 untracked (.gitignore, av15/, kebunate_ceo_mission_002/, package-lock.json).
- Nested repo: KnowingMindDashboard is an independent nested git repo (no submodule mapping), HEAD 0ced4dd, 4 untracked *.bak files; parent records modified gitlink. Do not touch.
- Preserve: siang-error.log/pm2.pid churn, checklist.json, OrderedDictionary, docs/*.html badge blocks are unrelated working state.

## 2. Worker implementation (V1/V2 naming: NOT FOUND)
- No file, symbol, or doc uses KM Coding Worker V1/V2, km_worker, worker_v2, coding_worker. Sole worker in repo is AV15 (untracked av15/).
- av15/receiver.py: uvicorn app; POST /line/webhook (HMAC, inbox/<job>.json attempts=0/max=3); GET /healthz; GET /queue.
- av15/worker.sh: flock single-flight; oldest inbox->doing/; attempts+poison exile; muse exec 1200s; done/<id>/{job,transcript,result} or requeue/failed/. No commit/push.
- av15/supervise.sh: loop worker.sh + heartbeat/collect.py every 60s; reconcile every 5th tick.
- av15/heartbeat/collect.py: schema KM_AV15_HEARTBEAT_TICK_V1; ring heartbeat_ring.jsonl cap 120 + AV15_last.json + heartbeat_seq; queue/muse/mcp/ollama/gh probes.
- av15/heartbeat/reconcile.sh: fresh tick + fleet-announce via vendored hub p02 bridge + gh-graphql readback on siango/KnowingMindApp#118; latest-only pending flag; drift SAME/CHANGED.
- av15/notify.py + linex.py: stages received|started|progress|done|failed; reply-then-push, exit-0-always.
- Non-workers excluded: kms-cdh-agent/main.py (GCS snapshot REST, no loop); kebunate_ceo_mission_002 intake script (contract JSON only).

## 3. Model integrations (all under av15/, mostly vendored hub)
- Muse: muse_response.py PROVIDERS=(meta,echo), muse exec CLI contract; worker.sh AV15_MUSE_PROVIDER=meta; preflight + heartbeat probes. Working path.
- Qwen/Ollama: qwen3_control.py DEFAULT qwen3:4b-q4_K_M @127.0.0.1:11434, /api/tags + /api/chat; main.py /v1/local-ai/status + EXPLAIN via DHAMMA_LOCAL_AI_ENDPOINT loopback; local_explanation.py loopback-only guard. Fixtures also name qwen3:4b, qwen2.5-coder:0.5b. Ollama-served only, no Qwen API.
- DeepSeek: NO runtime integration. Only explicit rejections (ai_worker_tools.py, kcsg1_ai_worker_status.py, tests assert WORKER_NOT_ALLOWLISTED).
- Routing: EP03 inventory-gated selection; DHAMMA_CAPABILITY_BRIDGE_V1 PCP-10 via project.select_federated_runtime + route_history; live authority Kebunate D1/KCC/MCPv4.

## 4. CI and tests
- CI: only .github/workflows/pages.yml (76 lines, pages deploy, NO test/lint job). Root has no package.json so Node steps skip; deploy path docs/.
- Tests: no root tests//Makefile/pyproject/pytest config. 57 test_*.py under av15/state/hub (stdlib unittest style) + 1 node .mjs test. No committed test command; implied python -m unittest, node <file>.

## 5. AV15 hardware (direct re-check matches)
- CPU 6x Intel i3-N305 (WSL2); RAM 4.8Gi (3.5Gi avail) + 4Gi swap; disk / 4% used, /mnt/c 96% used; GPU none visible in sandbox; ollama binary absent; OLLAMA_HOST unset; localhost:11434 empty (sandbox-namespaced loopback, needs host-side re-check).

## 6. SSOT / queue / lease / receipt
- SSOT: no in-repo integration. Only hit is generated av15/state/announce_last.json project_ssot_node=P02. Canonical P02 lives outside repo (control.kebunate.com / KnowingMindApp#118). No ledger module. No receipt code (one receipt hit is a donation-module plan note).
- Queue/heartbeat: authoritative paths listed in section 2. Durable file queue + heartbeat + reconcile-readback pattern is the reuse base.
- Lease/idempotency: NOT IMPLEMENTED. Closest: worker.lock flock, attempts/max_attempts exile, reply_used flag, latest-only PENDING supersede.

## 7. Gaps vs mission (Phases 1-6)
- P1: no provider abstraction; no DeepSeek adapter (local or API); no capability discovery/version tracking/per-task selection; no spending guard. Qwen-via-Ollama helpers exist but are Thai-chat scoped, not coding-task scoped.
- P2: no task admission/validation, bounded editing, patch gen, syntax check, unit/integration tests, repair loop, failure classification, backoff, checkpoint/resume, diff review, GitHub integration, evidence envelopes (Mission/Task/Owner/Input/Output/Status/Evidence/NextAction).
- P3: no lease/heartbeat per task, no idempotency keys, no crash/restart recovery proof, no resource/API spend limits, no health/progress reporting beyond existing tick.
- P4: no benchmark harness; variants B/C/D unrunnable today (no ollama, no DeepSeek backend, no budget).
- P5: P02 reconcile path exists (reconcile.sh + bridge) and must be reused; local-receipts-then-reconcile behavior must be added, not a new SSOT.
- P6: G9 needs a new CI test workflow (preserve pages.yml); G10 needs local receipt readback + P02 readback evidence.

## 8. Reuse / do-not-recreate list
- Reuse: av15 receiver/worker/supervise/heartbeat/reconcile/notify chain; p02_execution_bridge_v1 fleet-announce/readback; qwen3_control loopback + local_explanation guard patterns; muse exec contract; hub unittest style.
- Do not touch: KnowingMindDashboard nested repo, siang pm2 state, docs badge blocks, kebunate_ceo_mission_002, existing pages.yml behavior.
