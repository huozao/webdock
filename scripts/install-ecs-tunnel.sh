#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/install-ecs-tunnel.sh" >&2
  exit 1
fi

if [[ ! -f deploy/laptop/ecs-tunnel.env ]]; then
  cp deploy/laptop/ecs-tunnel.env.example deploy/laptop/ecs-tunnel.env
  chmod 600 deploy/laptop/ecs-tunnel.env
  echo "Created deploy/laptop/ecs-tunnel.env. Edit ECS_SSH_HOST and ECS_SSH_KEY before starting."
fi

install -m 0644 deploy/laptop/webdock-ecs-tunnel.service /etc/systemd/system/webdock-ecs-tunnel.service
systemctl daemon-reload
echo "Install complete. Start with: sudo systemctl enable --now webdock-ecs-tunnel"
