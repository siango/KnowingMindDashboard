#!/usr/bin/env bash
set -euo pipefail
pkg install -y git jq >/dev/null 2>&1 || true
REPO="${REPO:-$HOME/AndroidProjects/KnowingMindDashboard}"
REMOTE="${REMOTE:-https://github.com/siango/KnowingMindDashboard.git}"
ALIAS_NAME="${ALIAS_NAME:-$(uname -n)}"
STATUS="${STATUS:-ok}"

mkdir -p "$(dirname "$REPO")"
[ -d "$REPO/.git" ] || git clone "$REMOTE" "$REPO"
cd "$REPO"; mkdir -p docs
[ -f docs/data.json ] || echo '{"machines":[]}' > docs/data.json
[ -f docs/machine_aliases.json ] || echo '{}' > docs/machine_aliases.json

MACHINE_ID="$(uname -n)"
NOW="$(date --iso-8601=seconds)"

# --- fact helpers (best-effort) ---
termux_api() { command -v "$1" >/dev/null 2>&1 && "$@"; }
OS="$(getprop ro.build.version.release 2>/dev/null || echo Android)"
SDK="$(getprop ro.build.version.sdk 2>/dev/null || true)"
BRAND="$(getprop ro.product.brand 2>/dev/null || true)"
MODEL="$(getprop ro.product.model 2>/dev/null || true)"
ARCH="$(uname -m)"
KERNEL="$(uname -sr)"
CPU="$(getprop ro.hardware 2>/dev/null || echo unknown)"
BATJSON="$(termux_api termux-battery-status 2>/dev/null || echo '{}')"
BAT_PCT="$(echo "$BATJSON" | jq -r '.percentage // empty' 2>/dev/null || true)"
BAT_STAT="$(echo "$BATJSON" | jq -r '.status // empty' 2>/dev/null || true)"
PWR="$( [ "${BAT_STAT:-}" = "CHARGING" ] && echo charging || echo battery )"
# storage (internal data dir free/total)
ST_FREE="$(df -h ~ | awk 'NR==2{print $4}')"
ST_TOTAL="$(df -h ~ | awk 'NR==2{print $2}')"
# network (ssid/ip if termux-api available)
WIFIJSON="$(termux_api termux-wifi-connectioninfo 2>/dev/null || echo '{}')"
SSID="$(echo "$WIFIJSON" | jq -r '.ssid // empty' 2>/dev/null || true)"
IP="$(ip -4 addr show wlan0 2>/dev/null | awk "/inet/{print \$2}" | head -n1)"

PLATFORM="Android/Termux"
OS_STR="Android ${OS}${SDK:+ (SDK $SDK)}; ${BRAND:+$BRAND }${MODEL:+$MODEL}"
REPO_INFO="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)@$(git rev-parse --short HEAD 2>/dev/null || true)"

# alias map
tmp="$(mktemp)"; jq --arg id "$MACHINE_ID" --arg name "$ALIAS_NAME" '.[$id]=$name' docs/machine_aliases.json > "$tmp" && mv "$tmp" docs/machine_aliases.json

# read existing
exists="$(jq --arg id "$MACHINE_ID" '[.machines[]|select(.machine_id==$id)]|length' docs/data.json)"
ONBOARD="$([ "$exists" -eq 0 ] && echo true || echo false)"

# upsert with rich facts
tmp="$(mktemp)"
jq --arg id "$MACHINE_ID" --arg now "$NOW" --arg st "$STATUS" --arg name "$ALIAS_NAME" \
   --arg platform "$PLATFORM" --arg os "$OS_STR" --arg arch "$ARCH" --arg cpu "$CPU" \
   --arg battery "$BAT_PCT" --arg power "$PWR" --arg ssid "$SSID" --arg ip "$IP" \
   --arg stfree "$ST_FREE" --arg sttot "$ST_TOTAL" --arg repo "$REPO_INFO" --argjson onboard "$ONBOARD" '
  def upsert(m): (.machines |= (map(select(.machine_id != m.machine_id)) + [m]));
  def base: {
    machine_id:$id, name:$name, last_update:$now, status:$st,
    first_seen: (if $onboard then $now else null end),
    last_success: (if ($st|ascii_downcase)=="ok" then $now else null end),
    onboarding: $onboard,
    platform:$platform, os:$os, arch:$arch, cpu:$cpu,
    battery: (if $battery!="" then ($battery|tonumber) else null end),
    power:$power,
    network: {ssid: (if $ssid=="" then null else $ssid end), ip:(if $ip=="" then null else $ip end)},
    storage: {home_free:$stfree, home_total:$sttot},
    repo: $repo
  };
  if .machines then
    upsert((.machines[]|select(.machine_id==$id) // {} ) as $old |
           (base + { first_seen: ($old.first_seen // base.first_seen) }))
  else
    {"machines":[ base ]}
  end' docs/data.json > "$tmp" && mv "$tmp" docs/data.json

# commit/push with error capture
set +e
git add docs/data.json docs/machine_aliases.json
git commit -m "facts: ${MACHINE_ID} ($STATUS) @ ${NOW}" >/dev/null 2>&1
git pull --rebase --autostash
git push
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  # write last_error
  tmp="$(mktemp)"
  jq --arg id "$MACHINE_ID" --arg now "$NOW" \
     '.machines |= map(if .machine_id==$id then . + {last_error: "git_push_failed", status:"error", last_update:$now} else . end)' \
     docs/data.json > "$tmp" && mv "$tmp" docs/data.json
  git add docs/data.json && git commit -m "facts: note push error $MACHINE_ID @ $NOW" >/dev/null 2>&1 || true
fi

echo "✓ Updated $MACHINE_ID → $ALIAS_NAME ($STATUS)  onboard=$ONBOARD"
