#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/gokapi/.env"
COMPOSE_FILE="${ROOT_DIR}/deploy/gokapi/compose.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy deploy/gokapi/.env.example to deploy/gokapi/.env first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

mkdir -p "${GOKAPI_DATA_DIR:-/var/lib/gokapi/data}" "${GOKAPI_CONFIG_DIR:-/var/lib/gokapi/config}"

docker compose -p gokapi --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" pull
docker compose -p gokapi --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d
docker compose -p gokapi --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps

HEALTH_HOST="${GOKAPI_BIND:-127.0.0.1}"
if [[ "${HEALTH_HOST}" == "0.0.0.0" || "${HEALTH_HOST}" == "::" ]]; then
  HEALTH_HOST="127.0.0.1"
fi

curl -fsS --max-time 10 "http://${HEALTH_HOST}:${GOKAPI_HOST_PORT:-53842}/" >/dev/null
echo "Gokapi is reachable at http://${HEALTH_HOST}:${GOKAPI_HOST_PORT:-53842}/"
