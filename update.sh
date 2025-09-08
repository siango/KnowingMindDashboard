#!/usr/bin/env bash
set -euo pipefail
: "${DEBUG:=0}"
LOGFILE="$HOME/kms_dashboard_debug.log"
if [ "$DEBUG" = "1" ]; then set -x; fi
cd "$(dirname "$0")"

# 1) regenerate minimal field(s)
jq --arg now "$(date +'%Y-%m-%d %H:%M:%S %Z')" '.updated=$now' data.json > .tmp && mv .tmp data.json

# 2) commit & push
git add -A
git commit -m "chore(dashboard): auto update $(date +'%Y-%m-%d %H:%M:%S')" || true

CURRENT_REMOTE="$(git remote get-url origin)"
if [[ "$CURRENT_REMOTE" == https://github.com/* && -n "${GITHUB_TOKEN:-}" ]]; then
  git -c http.extraheader="Authorization: Bearer ${GITHUB_TOKEN}" push origin HEAD:$(git rev-parse --abbrev-ref HEAD)
else
  git push origin HEAD:$(git rev-parse --abbrev-ref HEAD)
fi
echo "[OK] update.sh completed at $(date)" | tee -a "$LOGFILE"
