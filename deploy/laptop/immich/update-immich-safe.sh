#!/usr/bin/env bash
set -euo pipefail

IMMICH_DIR="${IMMICH_DIR:-$HOME/immich-app}"
cd "$IMMICH_DIR"

"$(dirname "$0")/backup-immich-preflight.sh"

docker compose pull
docker compose up -d
"$(dirname "$0")/check-immich-health.sh"

echo "immich-update-ok"
