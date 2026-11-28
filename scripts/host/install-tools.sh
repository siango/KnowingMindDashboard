#!/usr/bin/env bash
set +x
set -euo pipefail
[[ $EUID != 0 ]] || { echo 'Run as your desktop user.' >&2; exit 1; }
sudo apt-get update
sudo apt-get install -y ca-certificates curl gh jq python3 python3-venv git xz-utils util-linux
# Install Node 24 in the user's home; preserve any system Node installation.
case $(uname -m) in x86_64) arch=x64;; aarch64|arm64) arch=arm64;; *) echo 'Unsupported CPU' >&2; exit 1;; esac
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
base=https://nodejs.org/dist/latest-v24.x
curl -fsSL --proto '=https' "$base/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
row=$(awk -v arch="$arch" '$2 ~ ("^node-v24\\.[0-9]+\\.[0-9]+-linux-" arch "\\.tar\\.xz$") {print}' "$tmp/SHASUMS256.txt")
[[ $(wc -l <<<"$row") == 1 && -n $row ]] || { echo 'Ambiguous Node release' >&2; exit 1; }
file=$(awk '{print $2}' <<<"$row")
curl -fsSL --proto '=https' "$base/$file" -o "$tmp/$file"
(cd "$tmp"; printf '%s\n' "$row" | sha256sum -c -)
mkdir -p "$HOME/.local/lib" "$HOME/.local/bin"
tar -xJf "$tmp/$file" -C "$HOME/.local/lib"
for bin in node npm npx; do ln -sfn "$HOME/.local/lib/${file%.tar.xz}/bin/$bin" "$HOME/.local/bin/$bin"; done
echo 'Tools installed. Before the next command: export PATH="$HOME/.local/bin:$PATH"'
