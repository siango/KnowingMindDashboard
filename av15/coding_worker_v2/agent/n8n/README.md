# KM n8n workflows (V3.1 Phase 1)

These are inactive, importable definitions only. No active n8n instance,
authenticated intake, task delivery, or receipt-to-central-SSOT transport is
verified in this source checkpoint.

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

The intake webhook definition does not configure authentication. Do not
activate it until authentication and receiver-side validation are implemented
and verified.

## Authority

n8n is intended to be an event/automation layer only. Task authority stays in
the KM queue (`QueueStore` + idempotency keys) and evidence is written to local
receipts; central SSOT transport is unverified. Intake currently reuses the
caller's task ID or creates an `ext-<timestamp>` fallback.
