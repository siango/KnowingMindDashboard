#!/usr/bin/env bash
set -euo pipefail
pkg install -y git jq >/dev/null 2>&1 || true
REPO="${REPO:-$HOME/AndroidProjects/KnowingMindDashboard}"
REMOTE="${REMOTE:-https://github.com/siango/KnowingMindDashboard.git}"
ALIAS_NAME="${ALIAS_NAME:-$(uname -n)}"
STATUS="${STATUS:-ok}"
mkdir -p "$(dirname "$REPO")"
[ -d "$REPO/.git" ] || git clone "$REMOTE" "$REPO"
cd "$REPO"
mkdir -p docs
[ -f docs/data.json ] || echo '{"machines":[]}' > docs/data.json
[ -f docs/machine_aliases.json ] || echo '{}' > docs/machine_aliases.json
MACHINE_ID="$(uname -n)"
NOW="$(date --iso-8601=seconds)"
tmp="$(mktemp)"; jq --arg id "$MACHINE_ID" --arg name "$ALIAS_NAME" '.[$id]=$name' docs/machine_aliases.json > "$tmp" && mv "$tmp" docs/machine_aliases.json
tmp="$(mktemp)"; jq --arg id "$MACHINE_ID" --arg now "$NOW" --arg st "$STATUS" --arg name "$ALIAS_NAME" '
  def upsert(m): (.machines |= (map(select(.machine_id != m.machine_id)) + [m]));
  if .machines then upsert({"machine_id":$id,"name":$name,"last_update":$now,"status":$st})
  else {"machines":[{"machine_id":$id,"name":$name,"last_update":$now,"status":$st}]} end' \
  docs/data.json > "$tmp" && mv "$tmp" docs/data.json
git add docs/data.json docs/machine_aliases.json
git commit -m "self-heal: ${MACHINE_ID} ($STATUS) @ ${NOW}" || true
git pull --rebase --autostash
git push
echo "✓ Updated ${MACHINE_ID} → ${ALIAS_NAME} (${STATUS})"
