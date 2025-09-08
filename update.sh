#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
MACHINE_ID="$(cat "$HOME/.kms_machine_id" 2>/dev/null || echo dev)"
now="$(date +'%Y-%m-%d %H:%M:%S %Z')"
host="$(uname -n 2>/dev/null || echo unknown)"
model="$(getprop ro.product.model 2>/dev/null || echo termux)"
os="$(uname -sr 2>/dev/null || echo unknown)"
branch="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD || echo main)"
ip="$(ip -o -4 addr show wlan0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
disk_free="$(df -h "$HOME" | awk 'NR==2{print $4}')"

mkdir -p "$ROOT/data/machines"

# 1) ensure latest from remote BEFORE writing, to reduce conflicts
git -C "$ROOT" fetch --all --prune || true
git -C "$ROOT" pull --rebase || true

# 2) write this machine snapshot
cat > "$ROOT/data/machines/${MACHINE_ID}.json" <<JSON
{
  "machine_id": "${MACHINE_ID}",
  "updated": "${now}",
  "host": "${host}",
  "model": "${model}",
  "os": "${os}",
  "branch": "${branch}",
  "ip": "${ip}",
  "disk_free": "${disk_free}",
  "doing": [
    {"text":"Dashboard auto-update cron","done":true}
  ],
  "metrics": {
    "progress_pct": 0,
    "valuation": null,
    "delta": 0
  },
  "notes": ""
}
JSON

# 3) update global timestamp
if [ -f "$ROOT/data.json" ]; then
  jq --arg now "$now" '.updated=$now' "$ROOT/data.json" > "$ROOT/.tmp_data.json" && mv "$ROOT/.tmp_data.json" "$ROOT/data.json"
else
  echo "{ \"updated\": \"$now\", \"items\": [], \"doing\": [] }" > "$ROOT/data.json"
fi

# 4) aggregate all machines -> machines.json and embed into data.json
if compgen -G "$ROOT/data/machines/*.json" > /dev/null; then
  jq -s '{machines: .}' "$ROOT"/data/machines/*.json > "$ROOT/machines.json"
  jq --slurpfile M "$ROOT/machines.json" '.machines = $M[0].machines' "$ROOT/data.json" > "$ROOT/.tmp_data.json" && \
    mv "$ROOT/.tmp_data.json" "$ROOT/data.json"
else
  echo '{ "machines": [] }' > "$ROOT/machines.json"
  jq '.machines = []' "$ROOT/data.json" > "$ROOT/.tmp_data.json" && mv "$ROOT/.tmp_data.json" "$ROOT/data.json"
fi

# 5) commit & push with simple retry for non-fast-forward
git -C "$ROOT" add -A
git -C "$ROOT" commit -m "chore(dashboard): ${MACHINE_ID} snapshot @ ${now}" || true

CURRENT_REMOTE="$(git -C "$ROOT" remote get-url origin)"
PUSH(){
  if [[ "$CURRENT_REMOTE" == https://github.com/* && -n "${GITHUB_TOKEN:-}" ]]; then
    git -C "$ROOT" -c http.extraheader="Authorization: Bearer ${GITHUB_TOKEN}" push origin "$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  else
    git -C "$ROOT" push origin "$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  fi
}
if ! PUSH; then
  git -C "$ROOT" pull --rebase || true
  PUSH || true
fi

echo "$(date +'%F %T') update from ${MACHINE_ID}" >> "$ROOT/.cron.log"
