#!/usr/bin/env bash
set -euo pipefail

IMMICH_DIR="${IMMICH_DIR:-$HOME/immich-app}"
cd "$IMMICH_DIR"

docker compose ps
curl -fsS "http://127.0.0.1:2283/api/server/ping" >/dev/null
echo "immich-health-ok"
