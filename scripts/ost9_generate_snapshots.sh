#!/data/data/com.termux/files/usr/bin/bash
# OST9 — Generate CDH snapshots (local) then you can upload/sync to Drive
set -euo pipefail
OUT_DIR="${1:-./KMS_CDH/snapshots}"
mkdir -p "$OUT_DIR"
NOW="$(date -Iseconds)"
cat > "$OUT_DIR/kms_snapshot_latest.json" <<JSON
{"schema":"kms_snapshot.v1","generated_at":"$NOW","projects":[],"services":[],"totals":{"tasks_all":0,"tasks_done":0,"issues_open":0}}
JSON
cat > "$OUT_DIR/kms_tasks_latest.json" <<JSON
{"schema":"kms_tasks.v1","items":[]}
JSON
cat > "$OUT_DIR/kms_heartbeat_latest.json" <<JSON
{"schema":"kms_heartbeat.v1","items":[]}
JSON
cat > "$OUT_DIR/kms_keys_meta_latest.json" <<JSON
{"schema":"kms_keys_meta.v1","items":[]}
JSON
printf "item,status,owner,updated_at,notes\nDaily snapshot generated,pending,system,%s,\nHeartbeat from devices received,pending,system,%s,\n" "$NOW" "$NOW" > "$OUT_DIR/kms_checks_latest.csv"
echo "- $NOW — local snapshot generated" > "$OUT_DIR/kms_releases_latest.md"
echo "[ok] Snapshots generated in $OUT_DIR"
