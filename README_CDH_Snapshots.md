
# KMS Central Data Hub — Snapshot Starter

This kit contains the canonical snapshot files read by both the team dashboard and ChatGPT (via Google Drive connector).
**Folder to upload on Drive:** `KMS_CDH/snapshots/` (private to the project).

## Files (atomic names)
- `kms_snapshot_latest.json` — overview: projects, services, totals
- `kms_tasks_latest.json` — tasks: done/wip/todo
- `kms_checks_latest.csv` — checklists (daily/weekly)
- `kms_heartbeat_latest.json` — devices/services health
- `kms_keys_meta_latest.json` — **metadata only** of secrets (NO secret values)
- `kms_releases_latest.md` — short changelog

## Usage
1) Upload the `KMS_CDH/snapshots/` folder to Google Drive under the same path.
2) Run one-shot scripts to regenerate snapshots locally while wiring cloud workflows later.
3) Ensure Drive is shared to the Google account connected to ChatGPT.

## Security
- Never store real secret values here. Only metadata (name, version, active/rotating, expires_at).
- Public dashboards must read from a separate `KMS_CDH/public/` folder, not this one.

Generated: 2025-09-17T10:53:45+00:00
