# AI 额度监控运行说明

## 运行边界

- quota-monitor 使用独立 Chrome profile、独立 `DISPLAY=:101`、独立 CDP `9224` 和 noVNC `6082`。
- 首次登录必须通过 noVNC 人工完成；只有人工确认登录完成后，才创建
  `/var/lib/webdock/quota_data/ATTACH_ENABLED`，采集器随后只读页面文字、进度条和截图。
- 采集器不自动登录、不点击账户操作、不使用 API token 读取额度。

## 网络与页面

- quota Chrome 必须继承 `CHROME_PROXY_SERVER`，webdock2 当前值为
  `http://host.docker.internal:7897`，与 ChatGPT 主浏览器共用 mihomo 出境代理。
- Codex 目标页为 `https://chatgpt.com/codex/cloud/settings/usage`（页面会落到
  `analytics#usage`）；Claude 目标页为 `https://claude.ai/settings/usage`。
- 页面加载后等待 `QUOTA_PAGE_SETTLE_SECONDS`（默认 5 秒），再读取 DOM；Cloudflare
  验证仍由人工在 noVNC 完成。

## 采集与展示

- 默认每 20–30 分钟随机采集一次，保留原始页面文字和 PNG 截图。
- console 卡片分别展示 5 小时限额、周限额、剩余/已用、绝对重置时间和倒计时；
  截图是解析异常时的最后证据。
- Codex 页面若没有单独提供 5 小时重置时间且剩余为 100%，展示“无需等待（额度充足）”，
  不从周额度时间推断 5 小时重置时间；周额度已耗尽时改展示“等待周额度重置后才能使用”。

### 重置时间按小节锚定，不按出现顺序

`claude_reset_sections` / `codex_reset_sections` 先用小节标题把页面文字切开
（Claude：`Current session` / `Weekly limits` / `Usage credits`；Codex：`5 hour usage limit` /
`Weekly usage limit` / `Credits remaining`），再在各自的块里找 `Resets …`。

<!-- nav-check-python: quota_monitor/core.py:claude_reset_sections -->
<!-- nav-check-python: quota_monitor/core.py:codex_reset_sections -->

⚠️ **按出现顺序取值（`findall` 后 `[0]` 当 5 小时、`[1]` 当周）自 2026-09-07 起确认会错位。**
页面在某个窗口还没用满时**不渲染那个窗口的 Resets 行**：Claude 会话未开始时显示
"Starts when a message is sent"，Codex 5 小时剩 100% 时同样没有 Resets。于是整体前移一格——
周重置被当成 5 小时重置，与额度窗口无关的 "Usage credits" 每月重置（"Oct 1"）被当成周重置。
实证在 capture id=48（错位）与 id=44（会话进行中、取值正确）。

### 时区：页面按浏览器时区渲染，容器是 UTC

容器**不设 `TZ`**，Chrome 因此按 UTC 渲染页面，`Resets Sep 7, 2026 2:24 AM` 指的是 UTC 时刻
（CST 10:24）。裸字符串每往下游传一层就被再猜一次时区：2026-09-07 实测 console 在 CST 浏览器里
把它当本地时间，整整早了 8 小时、显示成「已过期 / 约 0 分钟后重置」；`Oct 1` 更被 JS 解析成
2001-10-01。

`normalize_reset` 在**采集侧**把重置文案换算成带偏移的绝对时间，写进 `reset_at_iso` /
`weekly_reset_at_iso`；原始字符串保留在 `reset_at` / `weekly_reset_at` 作证据。
`/v1/quota/latest` 与 `/v1/quota/history` 两个字段都给，console 优先用 `*_iso`，
拿不到时原样回显字符串、不显示倒计时——**下游一律不得再从字符串猜绝对时间**。

<!-- nav-check-python: quota_monitor/core.py:normalize_reset -->

`QUOTA_DISPLAY_TZ`（默认 `Asia/Shanghai`）只影响飞书卡片里的文案渲染，不参与任何判定。

## 通知规则

- 早/中/晚日报照常发送。
- `quota.reset` **只由周额度变化触发**；5 小时窗口恢复不发送飞书通知。
- 周额度判定使用 `weekly_remaining` / `weekly_reset_at`，要求前后均为健康采集，
  并通过 `quota_events` 去重。
- ⚠️ **重置时间字符串变了不足以判重置**，必须同时看到剩余额度回升。2026-09-07 因为上面那个
  错位，`weekly_reset_at` 从 "Sat 10:00 AM" 变成 "Oct 1"，而 `weekly_remaining` 全程 75%
  没动，照样发出一条「周额度已重置」。判据现在落在剩余额度上（`weekly_reset_candidate`）。

<!-- nav-check-python: quota_monitor/core.py:weekly_reset_candidate -->

### 卡片排版

明细一律走 `segments`（每个平台一个 `section`，与 aliecs 流量日报同排版），`summary` 只留概述
或留空。⚠️ 飞书 channel 会把 `summary` 和 `segments` **依次**渲染，同一段文字既传 `summary`
又传一个 `text` segment，卡片里就会出现两遍——2026-09-07 的日报重复就是这么来的。

## 已知故障与修复

- 若 noVNC 页面显示“无法连接到服务器”，先看 quota 容器内的 `x11vnc.log`。
- Xvfb 与 x11vnc 必须有启动等待：直接并行启动会出现 `XOpenDisplay(":101") failed`，
  随后 websockify 反复报 `localhost:5902 connection refused`。当前 entrypoint 会等待
  `/tmp/.X11-unix/X101` 就绪后才启动 x11vnc。

## 发布

quota-monitor 随 webdock GitHub `main` 的不可变镜像发布；生产 compose 不再绑定本地源码，
设备只运行 GitHub Actions 构建出的镜像版本。
