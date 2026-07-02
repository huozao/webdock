#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/authentik/ecs-tunnel.env"
EXAMPLE_FILE="${ROOT_DIR}/deploy/authentik/ecs-tunnel.env.example"
SERVICE_FILE="${ROOT_DIR}/deploy/authentik/authentik-ecs-tunnel.service"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${EXAMPLE_FILE}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Created deploy/authentik/ecs-tunnel.env. Edit ECS_SSH_HOST and ECS_SSH_KEY before starting." >&2
  exit 1
fi

install -m 0644 "${SERVICE_FILE}" /etc/systemd/system/authentik-ecs-tunnel.service
systemctl daemon-reload
systemctl enable --now authentik-ecs-tunnel.service
