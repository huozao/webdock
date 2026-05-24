#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/laptop/.env"
COMPOSE_FILE="${ROOT_DIR}/deploy/laptop/compose.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy deploy/laptop/.env.example to deploy/laptop/.env first." >&2
  exit 1
fi

mkdir -p /var/lib/webdock/browser_data /var/log/webdock
docker compose -p webdock --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build
