#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/install-ubuntu.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

mkdir -p /var/lib/webdock/browser_data /var/log/webdock
systemctl enable --now docker

if [[ ! -f deploy/laptop/.env ]]; then
  cp deploy/laptop/.env.example deploy/laptop/.env
  echo "Created deploy/laptop/.env. Edit API_TOKEN, VNC_PASSWORD, and bind addresses before starting."
fi

install -m 0644 deploy/laptop/webdock.service /etc/systemd/system/webdock.service
systemctl daemon-reload
echo "Install complete. Start with: sudo systemctl enable --now webdock"
