#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/browser_data /app/logs/debug /app/.vnc
rm -f /app/browser_data/SingletonLock /app/browser_data/SingletonSocket /app/browser_data/SingletonCookie

VNC_PASSWORD="${VNC_PASSWORD:-changeme}"
if [[ "${#VNC_PASSWORD}" -gt 8 ]]; then
  echo "WARNING: VNC_PASSWORD is longer than 8 chars; VNC authentication may only use the first 8 chars."
fi
x11vnc -storepasswd "${VNC_PASSWORD}" /app/.vnc/passwd >/dev/null 2>&1

echo "webdock starting"
echo "API:   http://localhost:${API_PORT:-8000}"
echo "noVNC: http://localhost:${NOVNC_PORT:-6080}/vnc.html"
echo "API_TOKEN is used only for HTTP Authorization. VNC_PASSWORD is used only for noVNC login."
echo "Chrome runs as a normal supervised process and exposes local CDP on 127.0.0.1:9222."
echo "Open noVNC manually if ChatGPT needs login, CAPTCHA, or two-factor verification."

exec /usr/bin/supervisord -c /app/docker/supervisord.conf
