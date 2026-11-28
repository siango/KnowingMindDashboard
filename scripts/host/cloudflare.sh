#!/usr/bin/env bash
set +x
set -euo pipefail
usage() { echo 'cloudflare.sh PROJECT_DIR login|check|deploy|containers-check|containers-deploy'; }
[[ $# == 2 ]] || { usage; exit 2; }
cd "$1"
action=$2
[[ -f package.json && -f package-lock.json ]] || { echo 'Use a project with committed package.json + package-lock.json (pinned Wrangler).' >&2; exit 1; }
[[ -f wrangler.toml || -f wrangler.json || -f wrangler.jsonc ]] || { echo 'No existing Wrangler config; refusing automatic resource scaffolding.' >&2; exit 1; }
[[ $(node -p 'process.versions.node.split(".")[0]') -ge 22 ]] || { echo 'Node >=22 required; run install-tools.sh.' >&2; exit 1; }
npm ci
[[ -x node_modules/.bin/wrangler ]] || { echo 'Project must depend on Wrangler.' >&2; exit 1; }
wrangler=./node_modules/.bin/wrangler
case "$action" in
  login) exec "$wrangler" login --device;;
  check) "$wrangler" whoami; "$wrangler" deploy --dry-run;;
  deploy) "$wrangler" whoami; "$wrangler" deploy;;
  containers-check|containers-deploy)
    grep -Eq 'containers' wrangler.* || { echo 'No container configuration.' >&2; exit 1; }
    command -v docker >/dev/null || { echo 'Install Docker Engine first; see README.' >&2; exit 1; }
    docker info >/dev/null
    "$wrangler" whoami
    if [[ $action == containers-check ]]; then "$wrangler" deploy --dry-run; else "$wrangler" deploy; "$wrangler" containers list; fi;;
  *) usage; exit 2;;
esac
