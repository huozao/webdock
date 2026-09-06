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
  不从周额度时间推断 5 小时重置时间。

## 通知规则

- 早/中/晚日报照常发送。
- `quota.reset` **只由周额度变化触发**；5 小时窗口恢复不发送飞书通知。
- 周额度判定使用 `weekly_remaining` / `weekly_reset_at`，要求前后均为健康采集，
  并通过 `quota_events` 去重。

## 已知故障与修复

- 若 noVNC 页面显示“无法连接到服务器”，先看 quota 容器内的 `x11vnc.log`。
- Xvfb 与 x11vnc 必须有启动等待：直接并行启动会出现 `XOpenDisplay(":101") failed`，
  随后 websockify 反复报 `localhost:5902 connection refused`。当前 entrypoint 会等待
  `/tmp/.X11-unix/X101` 就绪后才启动 x11vnc。
