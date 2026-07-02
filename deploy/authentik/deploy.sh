#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/authentik/.env"
COMPOSE_FILE="${ROOT_DIR}/deploy/authentik/compose.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy deploy/authentik/.env.example to deploy/authentik/.env first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

mkdir -p \
  "${AUTHENTIK_POSTGRESQL_DIR:-/var/lib/authentik/postgresql}" \
  "${AUTHENTIK_DATA_DIR:-/var/lib/authentik/data}" \
  "${AUTHENTIK_CERTS_DIR:-/var/lib/authentik/certs}" \
  "${AUTHENTIK_TEMPLATES_DIR:-/var/lib/authentik/custom-templates}"

docker compose -p authentik --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" pull
docker compose -p authentik --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d
docker compose -p authentik --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps

HEALTH_HOST="${AUTHENTIK_BIND:-127.0.0.1}"
if [[ "${HEALTH_HOST}" == "0.0.0.0" || "${HEALTH_HOST}" == "::" ]]; then
  HEALTH_HOST="127.0.0.1"
fi

curl -fsS --max-time 30 "http://${HEALTH_HOST}:${COMPOSE_PORT_HTTP:-9000}/-/health/live/" >/dev/null
echo "authentik is reachable at http://${HEALTH_HOST}:${COMPOSE_PORT_HTTP:-9000}/"
