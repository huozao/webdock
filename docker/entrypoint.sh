#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/browser_data /app/browser_data/feishu-sync /app/logs/debug /app/.vnc
# Singleton* 里写的是**创建它的那个容器的 hostname**，容器一重建就变成「另一台计算机」，
# Chrome 于是拒绝启动并锁住 profile。ChatGPT 侧一直在这里清，所以它每次都能自愈；
# Feishu profile 从来没人清，2026-09-09 一次 WSL 重启后就卡在
# "The profile appears to be in use by another Google Chrome process (…) on another computer"
# 整整一天没人发现。两个 profile 各清各的，绝不互相跨目录。
for profile in /app/browser_data /app/browser_data/feishu-sync; do
  rm -f "$profile/SingletonLock" "$profile/SingletonSocket" "$profile/SingletonCookie"
done

VNC_PASSWORD="${VNC_PASSWORD:-changeme}"
if [[ "${#VNC_PASSWORD}" -gt 8 ]]; then
  echo "WARNING: VNC_PASSWORD is longer than 8 chars; VNC authentication may only use the first 8 chars."
fi
x11vnc -storepasswd "${VNC_PASSWORD}" /app/.vnc/passwd >/dev/null 2>&1

echo "webdock starting"
echo "API:   http://localhost:${API_PORT:-8000}"
echo "noVNC: http://localhost:${NOVNC_PORT:-6080}/vnc.html"
echo "Feishu noVNC: http://localhost:${FEISHU_NOVNC_PORT:-6081}/vnc.html"
echo "API_TOKEN is used only for HTTP Authorization. VNC_PASSWORD is used only for noVNC login."
echo "Chrome runs as a normal supervised process and exposes local CDP on 127.0.0.1:9222."
echo "Feishu synchronization uses an independent Chrome/display when its companion process is enabled."
echo "Open noVNC manually if ChatGPT needs login, CAPTCHA, or two-factor verification."

exec /usr/bin/supervisord -c /app/docker/supervisord.conf
