# KM n8n workflows (V3.1 Phase 1)

Importable definitions only. No running instance is claimed: the owner
imports these into the existing n8n instance (`n8n 1.109.1` binary
verified on AV15; no WSL instance data exists yet).

## Files

- `km_task_intake.json` — webhook intake, maps to `KM_CODING_TASK_V1`
  with the caller's `task_id` / `idempotency_key` preserved.
- `km_task_monitor.json` — every 5m polls KM status, alerts on failures.
- `km_qc_events.json` — QC webhook, alerts only on `qc_passed=false`.
- `km_github_ci_monitor.json` — every 10m lists workflow runs, alerts
  on non-success conclusions.
- `km_exception_notify.json` — exception webhook to the alert channel.
- `km_daily_summary.json` — daily 07:00 queue + CI summary.

## Required environment on the n8n host

- `KM_INTAKE_URL`, `KM_STATUS_URL`, `KM_ALERT_URL`
- `GH_REPO` (e.g. `siango/KnowingMindDashboard`), `GH_TOKEN`

## Authority

n8n is an event/automation layer only. Task authority stays in the KM
queue (`QueueStore` + idempotency keys) and evidence stays in SSOT
receipts. n8n never invents task IDs: intake reuses the caller's ID or a
single `ext-<timestamp>` fallback that the queue then owns.
