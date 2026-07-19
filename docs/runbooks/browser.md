# Runbook：WebDock 浏览器 / ChatGPT 登录态 / Cloudflare 排障

## ⛔ 先读红线

ChatGPT 登录与 Cloudflare 验证必须人工在 noVNC 完成，自动化必须 detach（全文见 `AliECS/AGENTS.md` 首节）。原因：Playwright 连 CDP 泄漏 `Runtime.enable`，Cloudflare 判定自动化后人工点击也无限循环。任何涉及浏览器启动、attach/detach、容器重建的改动先与用户确认。

## 关键机制（改代码前必知）

- **完成判定权威信号 = stop 按钮**（`data-testid='stop-button'`，选择器已收窄）。按钮在 = 没结束，绝不提前返回；别改回以 `.result-streaming` 为准，否则"只收开场白"截断 bug 回归。
- 回复取全部 `.markdown` 块拼接（开场白和正文可能是同 turn 两个独立块）。
- detector 锚 `conversation-turn`；CDP 连接用 patchright（非原版 playwright）。
- 长思考超时链：cloud-provider idle watchdog 是 B 根因，`baseUrl→172.17.0.1` 判 local 禁 watchdog。
- 登录态在 `browser_data/` 卷；重建容器登录态可存活（卷保住，无需重登），但**改浏览器启动逻辑的重建必须先问用户**。
- 图改图：图片文件 pill 点击=开预览层非下载；预览层兜底抓图按 MEDIA 投递；copy 按钮=正向完成信号（生成中不出现），缺失时 +8s 宽限。
- OpenClaw monitor 串行投递图片（慢是设计不是 bug）；⛔ bridge 反转合并别重试。
- 文件附件：捕获正则必须容忍 ` (image/*)`；context-summary 历史块要先剥离防死循环。

## 症状表

| 症状 | 先查 | 处置 |
|---|---|---|
| 开机后一直不回复 | noVNC 看 Chrome 是否卡「恢复页面」提示 | 人工关浏览器 → 自动重开干净 Chrome 即自愈；勿自动登录 |
| 回复半截/只有开场白 | detector 完成判定是否被改动 | 见上方 stop 按钮红线 |
| RESPONSE_TIMEOUT | 是否长思考（推理模型）；watchdog 是否被禁 | 正常长思考等即可；反复超时查 baseUrl 判定 |
| Cloudflare 无限验证循环 | 自动化是否 attach 着 | detach 后人工过验证 |
| 多图请求后全线卡死 | 单 worker 被堵（142-153s/13图）；healthz 假绿 | 等释放或重启容器；车道隔离测试须测对车道 |
| webdock2 整机失联 | WSL 是否活：容器 Up 时长 < 命令年龄 = 假活 | 保活任务已改开机+S4U+`wsl sleep infinity` 常驻（07-12） |

## 排障工具

- CDP 旁路：patchright `connect_over_cdp` 容器 `:9222` dump DOM。**⚠️ 9222 是 ChatGPT 生产实例别乱动**（webdock2 上 9223 是另一独立 Chrome）。
- 存档：`/var/log/webdock/archive/<UTC日期>.jsonl`，查 `status` / `outbound.chars`。
- webdock2 执行 Linux 命令：`ssh webdock2` 进的是 PowerShell，须 `wsl -d Ubuntu-24.04-WebDock -- <cmd>`；复杂 PS 用 `-EncodedCommand`。

## 部署

- 换镜像：在 infra `secrets/webdock<N>.enc.env` 更新 `WEBDOCK_IMAGE`，推送并在设备执行 `render.sh webdock<N>` + restart。`.env` 是渲染产物，禁止直接长期修改；机型化 unit 从同一渲染流程生成。
- `webdock.service` 已加 `ExecStartPre=-pull`（自愈拉镜像）；新机装 `install-ubuntu.sh` 自带。
- ⚠️ CI 只在 PR 跑 pytest，直推 main 不跑 → 直推前必须本地 `pytest -q`。
- ⚠️ Windows 侧写 JSON 必须显式 utf-8，否则 API 吃坏 body。
