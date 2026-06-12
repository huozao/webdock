#!/usr/bin/env bash
set -euo pipefail

IMMICH_DIR="${IMMICH_DIR:-$HOME/immich-app}"
cd "$IMMICH_DIR"

if [[ ! -f .env ]]; then
  echo "missing .env in $IMMICH_DIR" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

UPLOAD_LOCATION="${UPLOAD_LOCATION:-./library}"
DB_DATA_LOCATION="${DB_DATA_LOCATION:-./postgres}"
BACKUP_DIR="$UPLOAD_LOCATION/backups"

if [[ ! -d "$UPLOAD_LOCATION" ]]; then
  echo "missing Immich upload directory: $UPLOAD_LOCATION" >&2
  exit 1
fi

if [[ ! -d "$DB_DATA_LOCATION" ]]; then
  echo "missing Immich database directory: $DB_DATA_LOCATION" >&2
  exit 1
fi

if [[ ! -d "$BACKUP_DIR" ]]; then
  echo "missing Immich database backup directory: $BACKUP_DIR" >&2
  exit 1
fi

if ! find "$BACKUP_DIR" -type f -mtime -2 ! -name '.*' 2>/dev/null | grep -q .; then
  echo "no recent Immich database backup found under $BACKUP_DIR" >&2
  exit 1
fi

echo "immich-backup-preflight-ok"
