#!/usr/bin/env bash
set -euo pipefail
[[ $EUID != 0 ]] || { echo 'Run as your desktop user.' >&2; exit 1; }
if command -v docker >/dev/null; then
  docker info >/dev/null || { echo 'Docker exists but is not accessible to this user. Resolve the existing service/permissions first.' >&2; exit 1; }
  echo 'Reusing existing Docker.'; exit 0
fi
. /etc/os-release
[[ $ID == ubuntu ]] || { echo 'Ubuntu only.' >&2; exit 1; }
for package in docker.io podman-docker containerd runc; do
  if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
    echo "Conflicting $package exists. Preserve/resolve the existing runtime first." >&2; exit 1
  fi
done
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$(id -un)"
echo 'Docker installed. Log out/in before using Wrangler with Docker. The docker group grants host administration privileges.'
