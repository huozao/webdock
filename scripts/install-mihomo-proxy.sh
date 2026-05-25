#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/laptop/mihomo.env"
EXAMPLE_FILE="${ROOT_DIR}/deploy/laptop/mihomo.env.example"
TEMPLATE_FILE="${ROOT_DIR}/deploy/laptop/mihomo-config.yaml.template"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/install-mihomo-proxy.sh" >&2
  exit 1
fi

if ! command -v mihomo >/dev/null 2>&1 && [[ ! -x /usr/local/bin/mihomo ]]; then
  echo "mihomo is not installed. Install /usr/local/bin/mihomo first, then rerun this script." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${EXAMPLE_FILE}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Created ${ENV_FILE}. Fill in subscription URL and primary node values, then rerun." >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
. "${ENV_FILE}"
set +a

required_vars=(
  MIHOMO_MIXED_PORT
  MIHOMO_CONFIG_DIR
  MIHOMO_SUBSCRIPTION_URL
  MIHOMO_PRIMARY_NAME
  MIHOMO_PRIMARY_SERVER
  MIHOMO_PRIMARY_PORT
  MIHOMO_PRIMARY_UUID
  MIHOMO_PRIMARY_SERVERNAME
  MIHOMO_PRIMARY_PUBLIC_KEY
  MIHOMO_PRIMARY_SHORT_ID
)

for name in "${required_vars[@]}"; do
  if [[ -z "${!name:-}" || "${!name}" == replace_with_* ]]; then
    echo "Missing required value: ${name}" >&2
    exit 1
  fi
done

install -d -m 0755 "${MIHOMO_CONFIG_DIR}" "${MIHOMO_CONFIG_DIR}/providers"

python3 - "$TEMPLATE_FILE" "${MIHOMO_CONFIG_DIR}/config.yaml" <<'PY'
import os
import sys
from pathlib import Path

template = Path(sys.argv[1]).read_text(encoding="utf-8")
for key, value in os.environ.items():
    if key.startswith("MIHOMO_"):
        template = template.replace("${" + key + "}", value)
Path(sys.argv[2]).write_text(template, encoding="utf-8")
PY

chmod 600 "${MIHOMO_CONFIG_DIR}/config.yaml"

if command -v mihomo >/dev/null 2>&1; then
  mihomo_bin="$(command -v mihomo)"
else
  mihomo_bin="/usr/local/bin/mihomo"
fi

"${mihomo_bin}" -t -d "${MIHOMO_CONFIG_DIR}"

cat >/etc/systemd/system/mihomo.service <<UNIT
[Unit]
Description=Mihomo proxy service for webdock
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${mihomo_bin} -d ${MIHOMO_CONFIG_DIR}
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable mihomo.service
systemctl restart mihomo.service
