#!/usr/bin/env bash
# webdock.service 的启动前置断言：.env 里 pin 的镜像必须本地可用。
#
# ⚠️ 为什么需要它：上一行的 `ExecStartPre=-… pull` 带前导 `-`，pull 失败不致命。
# 那个设计假设「离线主机已经有这些镜像」。2026-09-09 该假设被击穿——.env 刚把
# WEBDOCK_IMAGE 换成一个设备上还没有的 tag，恰好网卡驱动坏掉整机断网，于是
# pull 失败被吞、`up -d` 照常执行、compose 为应用新配置先删掉了正在健康运行的
# 旧容器，然后才报 "No such image" 退出。结果两个容器全没，服务停了 45 分钟。
#
# 判据落在连接处：ExecStart 真正的前置条件不是「pull 跑过了」，而是「pin 的镜像
# 本地拿得到」。拿不到就中止，让旧容器继续跑——中止启动的代价远小于删光容器。
#
# 只读 .env 里需要的键，不 source 整个文件（那会把密钥带进环境）。
set -euo pipefail

env_file="${1:-/opt/webdock/deploy/laptop/.env}"

if [[ ! -r "$env_file" ]]; then
  echo "require-pinned-images: cannot read $env_file" >&2
  exit 1
fi

read_key() {
  # 取最后一次赋值，与 docker compose 读 env 文件的口径一致
  sed -n "s/^$1=//p" "$env_file" | tail -1
}

images=()
webdock_image="$(read_key WEBDOCK_IMAGE)"
[[ -n "$webdock_image" ]] && images+=("$webdock_image")

# quota-monitor 由 COMPOSE_PROFILES 门控，没开就不该校验它的镜像
profiles=",$(read_key COMPOSE_PROFILES),"
if [[ "$profiles" == *,quota,* ]]; then
  quota_image="$(read_key QUOTA_IMAGE)"
  [[ -n "$quota_image" ]] && images+=("$quota_image")
fi

missing=()
for image in "${images[@]:-}"; do
  [[ -n "$image" ]] || continue
  docker image inspect "$image" >/dev/null 2>&1 || missing+=("$image")
done

if (( ${#missing[@]} > 0 )); then
  echo "require-pinned-images: pinned image(s) not available locally: ${missing[*]}" >&2
  echo "require-pinned-images: refusing to start so the running containers are not torn down." >&2
  exit 1
fi
