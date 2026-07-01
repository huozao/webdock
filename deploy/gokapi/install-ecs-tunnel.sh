#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/gokapi/ecs-tunnel.env"
EXAMPLE_FILE="${ROOT_DIR}/deploy/gokapi/ecs-tunnel.env.example"
SERVICE_FILE="${ROOT_DIR}/deploy/gokapi/gokapi-ecs-tunnel.service"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${EXAMPLE_FILE}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Created deploy/gokapi/ecs-tunnel.env. Edit ECS_SSH_HOST and ECS_SSH_KEY before starting." >&2
  exit 1
fi

install -m 0644 "${SERVICE_FILE}" /etc/systemd/system/gokapi-ecs-tunnel.service
systemctl daemon-reload
systemctl enable --now gokapi-ecs-tunnel.service
