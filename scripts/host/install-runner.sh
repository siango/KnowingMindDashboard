#!/usr/bin/env bash
# Run as the ordinary Ubuntu desktop user, never with sudo bash.
set +x
set -euo pipefail
umask 077
die() { echo "ERROR: $*" >&2; exit 1; }
[[ ${1:-} != --help ]] || { echo 'REPO=owner/repo RUNNER_DIR=/persistent/path bash install-runner.sh'; exit 0; }
[[ $EUID != 0 ]] || die 'Run as your desktop user; this script invokes sudo only for the service.'
[[ $(uname -s) == Linux ]] || die 'Ubuntu with systemd is required.'
systemctl show --property=Version --value >/dev/null || die 'systemd is not running.'
for cmd in gh python3 curl tar sha256sum flock; do command -v "$cmd" >/dev/null || die "Missing $cmd; run install-tools.sh first."; done
repo=${REPO:-siango/KnowingMindDashboard}
[[ $repo =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die 'Invalid REPO.'
gh auth status --hostname github.com >/dev/null 2>&1 || die 'Run: gh auth login --hostname github.com'
user=$(id -un)
url="https://github.com/$repo"
# Locate configured runners before allocating a new directory.
if [[ -z ${RUNNER_DIR:-} ]]; then
  matches=()
  while IFS= read -r -d '' file; do
    configured=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("gitHubUrl", "").rstrip("/"))' "$file")
    [[ ${configured,,} != ${url,,} ]] || matches+=("${file%/.runner}")
  done < <(find "$HOME" -maxdepth 6 -type f -name .runner -print0 2>/dev/null)
  ((${#matches[@]} <= 1)) || die 'Multiple matching runners; set RUNNER_DIR to the intended existing one.'
  RUNNER_DIR=${matches[0]:-"$HOME/KnowingMind/runners/github"}
fi
mkdir -p "$RUNNER_DIR"
RUNNER_DIR=$(realpath "$RUNNER_DIR")
[[ $(stat -c %U "$RUNNER_DIR") == "$user" ]] || die 'Runner directory must belong to the current user.'
fstype=$(findmnt -n -o FSTYPE -T "$RUNNER_DIR")
case "$fstype" in overlay|tmpfs|ramfs) die 'Choose RUNNER_DIR on a persistent USB/home filesystem; live-session storage will disappear.';; esac
exec 9>"$RUNNER_DIR/.install.lock"
flock -n 9 || die 'Another installer is running.'
cd "$RUNNER_DIR"
if [[ -f .runner ]]; then
  configured=$(python3 -c 'import json; print(json.load(open(".runner"))["gitHubUrl"].rstrip("/"))')
  [[ ${configured,,} == ${url,,} ]] || die 'Existing runner belongs to another repository.'
  [[ -x svc.sh ]] || die 'Configured runner is incomplete; restore its original runner files.'
else
  [[ ! -f config.sh ]] || die 'Incomplete installation found. Inspect this directory before retrying.'
  name=${RUNNER_NAME:-"$(hostname -s)-usb"}
  existing=$(gh api --paginate "repos/$repo/actions/runners?per_page=100" --jq '.runners[].name')
  if grep -Fxq -- "$name" <<<"$existing"; then die 'Runner name already registered. Restore its original directory or select RUNNER_NAME; no replacement was performed.'; fi
  case $(uname -m) in x86_64) arch=x64;; aarch64|arm64) arch=arm64;; *) die 'Supported CPU: x86_64 / arm64';; esac
  release=$(gh api repos/actions/runner/releases/latest)
  asset=$(python3 -c 'import json,sys; a=json.load(sys.stdin); xs=[x for x in a["assets"] if x["name"].startswith("actions-runner-linux-"+sys.argv[1]+"-") and x["name"].endswith(".tar.gz")]; assert len(xs)==1; x=xs[0]; d=x.get("digest", ""); assert d.startswith("sha256:"), "Missing upstream SHA256 digest"; print(x["browser_download_url"]); print(d[7:])' "$arch" <<<"$release")
  download=$(head -n1 <<<"$asset")
  checksum=$(tail -n1 <<<"$asset")
  [[ $download == https://github.com/actions/runner/releases/download/* && $checksum =~ ^[a-f0-9]{64}$ ]] || die 'Invalid upstream asset metadata.'
  archive=$(mktemp)
  trap 'rm -f "${archive:-}"; unset ACTIONS_RUNNER_INPUT_TOKEN' EXIT
  curl --fail --location --proto '=https' --tlsv1.2 "$download" -o "$archive"
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum -c -
  tar -xzf "$archive"
  sudo ./bin/installdependencies.sh
  # The registration token is passed through the official runner input env,
  # never printed or placed in shell history / process arguments.
  export ACTIONS_RUNNER_INPUT_TOKEN
  ACTIONS_RUNNER_INPUT_TOKEN=$(gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token)
  [[ -n $ACTIONS_RUNNER_INPUT_TOKEN ]] || die 'No registration token returned.'
  ./config.sh --unattended --url "$url" --name "$name" --labels "${RUNNER_LABELS:-km-fleet,nonprod,kmcp}" --work _work
  unset ACTIONS_RUNNER_INPUT_TOKEN
fi
[[ -f .service ]] || sudo ./svc.sh install "$user"
service=$(cat .service)
systemctl is-active --quiet "$service" || sudo ./svc.sh start
sudo ./svc.sh status
echo "Local service installed. GitHub status is the final availability check:"
gh api --paginate "repos/$repo/actions/runners?per_page=100" --jq '.runners[] | {name,status,busy,labels:[.labels[].name]}'
