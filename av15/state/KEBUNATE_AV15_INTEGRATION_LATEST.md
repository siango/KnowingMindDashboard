# KEBUNATE AV15 — Canonical Mission Checkpoint
Updated from owner-supplied AV15 terminal evidence, 2026-11-21 03:38 Asia/Bangkok.
Mission: G0-G1-MINIMUM-SELLABLE-DEMO / existing KMCW V3 + KMCO 132 + KEBUNATE Core + KMNW + MCP V5 integration.
Node: AV15 (siango, Ubuntu).
Scope: status checkpoint ONLY; not a production authorization, release or independent acceptance.
Source of truth: original AV15 terminal output and PostgreSQL readback; this file is an evidence projection, not database execution truth.

## VERIFIED from AV15 terminal output
- Existing KMCW ToolRegistry called canonical `kmco.p01.evidence`, executing P01-M01 through P01-M05 on real file.
- Source file SHA256 and exact citation matched.
- Existing `km_microops` database reached via non-superuser `siango`, local peer libpq service.
- `psycopg` 3.1.17 available.
- KMCW automatic KMCO usage event and 4-part Core Memory were inserted with exact DB readback.
- KMCW `kmco.core.recall` returned same scoped derived memory.
- 2026-11-21 03:36 local execution: `LOCAL_CORE_E2E=PASS`, `DB_USAGE_EXACT_READBACK=PASS`, `DB_CORE_FOUR_PART_READBACK=PASS`, `KMCW_CORE_RECALL=PASS`, exit 0.
- 2026-11-21 03:38 existing database inventory: `circuit.registry=0`, `circuit.bindings=0`, `circuit.relations=0`, `circuit.routes=0`, `circuit.usage_events=2`, `circuit.core_memory=2`.
- KMCO snapshot passed 132/132 *design* validations and local P01/P02 test QC (25 tests at last full source QC). 132/132 executable handlers NOT established.

## CURRENT BLOCKERS / NOT PROVEN
1. KMCO canonical data not projected into existing `circuit.registry` (0 rows). No invented runnable grants/handler bindings.
2. Real KMNW ACK/lease/useful-work/result integration not verified. No KMNW process found via last AV15 check, which does not prove absence of any installed source/service.
3. Existing Cloudflare tunnel active, but no TCP listener on AV15 localhost:18856 at 03:38. HTTP edge anonymous response 401 (protected), which is NOT authenticated MCP tool acceptance.
4. @KnowingMindDhamma project health / federated runtime connector calls returned Internal Error in ChatGPT session. No successful ChatGPT→MCP V5→AV15 script execution proof.
5. pgvector 0.6.0 extension installed in `km_microops`; there are zero vector columns/HNSW/IVFFlat indexes in the inspected `circuit` schema; semantic search has not been tested.
6. Four-part engineering memory stores cause as NOT_DIAGNOSED and state as UNVERIFIED; no independent QC or signed receipt.
7. Persistent DB runtime environment beyond the interactive shell, and approved MCP production authority remain unverified.

## KEBUNATE Core mapping (engineering only; never Canon)
- รู้: actual input, source SHA, tool result and provenance.
- จำ: `circuit.usage_events` and `circuit.core_memory`, exact readback and bounded mission/task recall.
- ทำ: KMCW ToolRegistry invokes only real authorized KMCO handlers.
- อริยสัจสี่: observed problem (dukkha), evidenced/hypothesized cause (samudaya), measurable acceptance (nirodha), executed remedy/path (magga). Not an independent success claim.

## FIRST UNFINISHED STEP
Project 132 design records into the EXISTING registry with exact provenance and DESIGN_ONLY state, and locate verified KMNW runtime / MCP V5 origin authority before proposing any write or remote command. Do not fabricate 132 executed handlers, leases, embeddings, fake receipts or successful remote tool calls.

## NEXT ACTION
Reuse KMCW, KMCO, KMNW, KnowingMindDhamma, MCP V5, Cloudflare and `km_microops` only. Prove each missing connection with exact AV15 evidence, and produce independent receipt after a real cross-system run. No new gateway, database, queue, worker or authority expansion.

## Acceptance
`LOCAL_CORE_E2E=PASS`. `FULL_SIX_SYSTEM_ACCEPTANCE=BLOCKED`. Source/commit progress does not equal live runtime or independent QC.
