#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
jq --arg now "$(date +'%Y-%m-%d %H:%M:%S %Z')" '.updated=$now' data.json > .tmp && mv .tmp data.json
git add -A
git commit -m "chore(dashboard): auto update $(date +'%Y-%m-%d %H:%M:%S')" || true
CURRENT_REMOTE="$(git remote get-url origin)"
if [[ "$CURRENT_REMOTE" == https://github.com/* && -n "${GITHUB_TOKEN:-}" ]]; then
  git -c http.extraheader="Authorization: Bearer ${GITHUB_TOKEN}" push origin HEAD:$(git rev-parse --abbrev-ref HEAD)
else
  git push origin HEAD:$(git rev-parse --abbrev-ref HEAD)
fi
echo "$(date +'%F %T') update done" >> .cron.log
