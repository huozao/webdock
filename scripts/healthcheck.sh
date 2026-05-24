#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/laptop/.env"
COMPOSE_FILE="${ROOT_DIR}/deploy/laptop/compose.yml"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

docker compose -p webdock --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps
curl -fsS http://127.0.0.1:${HOST_API_PORT:-18000}/healthz
