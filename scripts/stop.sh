#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/laptop/.env"
COMPOSE_FILE="${ROOT_DIR}/deploy/laptop/compose.yml"

docker compose -p webdock --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" down
