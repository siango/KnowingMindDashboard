#!/usr/bin/env bash
set -euo pipefail
REPO="${REPO:-$HOME/KnowingMindDashboard}"
REMOTE="${REMOTE:-https://github.com/siango/KnowingMindDashboard.git}"
ALIAS_NAME="${ALIAS_NAME:-$(hostname)}"
STATUS="${STATUS:-ok}"
command -v jq >/dev/null 2>&1 || { echo "please install jq (e.g. apt/yum/brew)"; exit 1; }
mkdir -p "$(dirname "$REPO")"
[ -d "$REPO/.git" ] || git clone "$REMOTE" "$REPO"
cd "$REPO"; mkdir -p docs
[ -f docs/data.json ] || echo '{"machines":[]}' > docs/data.json
[ -f docs/machine_aliases.json ] || echo '{}' > docs/machine_aliases.json

MACHINE_ID="$(hostname)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ARCH="$(uname -m)"; KERNEL="$(uname -sr)"
OS="$( (lsb_release -d 2>/dev/null | cut -f2-) || sw_vers 2>/dev/null || uname -s )"
CPU="$(uname -p 2>/dev/null || echo unknown)"
PLATFORM="Linux/macOS"
IP="$(hostname -I 2>/dev/null | awk "{print \$1}" || true)"

tmp="$(mktemp)"; jq --arg id "$MACHINE_ID" --arg name "$ALIAS_NAME" '.[$id]=$name' docs/machine_aliases.json > "$tmp" && mv "$tmp" docs/machine_aliases.json

exists="$(jq --arg id "$MACHINE_ID" '[.machines[]|select(.machine_id==$id)]|length' docs/data.json)"
ONBOARD="$([ "$exists" -eq 0 ] && echo true || echo false)"

tmp="$(mktemp)"
jq --arg id "$MACHINE_ID" --arg now "$NOW" --arg st "$STATUS" --arg name "$ALIAS_NAME" \
   --arg platform "$PLATFORM" --arg os "$OS $KERNEL" --arg arch "$ARCH" --arg cpu "$CPU" --arg ip "$IP" '
  def upsert(m): (.machines |= (map(select(.machine_id != m.machine_id)) + [m]));
  def base: {
    machine_id:$id, name:$name, last_update:$now, status:$st,
    first_seen: (if '"$ONBOARD"' then $now else null end),
    last_success: (if ($st|ascii_downcase)=="ok" then $now else null end),
    onboarding: '"$ONBOARD"',
    platform:$platform, os:$os, arch:$arch, cpu:$cpu,
    network:{ip:(if $ip=="" then null else $ip end)}
  };
  if .machines then
    upsert((.machines[]|select(.machine_id==$id) // {} ) as $old |
           (base + { first_seen: ($old.first_seen // base.first_seen) }))
  else
    {"machines":[ base ]}
  end' docs/data.json > "$tmp" && mv "$tmp" docs/data.json

git add docs/data.json docs/machine_aliases.json
git commit -m "facts: ${MACHINE_ID} ($STATUS) @ ${NOW}" >/dev/null 2>&1 || true
git pull --rebase --autostash
git push
echo "✓ Updated $MACHINE_ID → $ALIAS_NAME ($STATUS)  onboard=$ONBOARD"
