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
- **生成文档 pill 点击 = 开预览飞出层，不触发 download**（07-27）。层是 `data-testid=stage-thread-flyout` / `screen-threadFlyOut`，自带 `aria-label=Download`；**Escape 关不掉它**（实测 width 751 扛过多次 Escape），必须点 `data-testid=close-button`。层不关会盖住会话，下一轮永远等不到完成信号 → 整条 lane 被 wedge。每轮发送前会清一次残留层。
- **idle 判定必须尊重 stop 按钮**（07-27）。ChatGPT 跑代码生成文档时页面完全静止（文本冻结、stop 亮着），progress signature 不变；旧逻辑在 `soft_deadline + idle_timeout` 处判死，实测 173/173/188/191s 全灭，而页面本身 4m49s 才生成完。
- **ChatGPT 自己的失败横幅**（`Something went wrong while generating the response` + Retry）现由 `generation_error_text()` 识别，立即报 `GENERATION_FAILED`。只认最后一轮的横幅——历史失败轮会永远留在会话里，匹配到就会毒化之后每个请求。
- **同车道不再长时间排队**：普通请求等待同一 `lane.key` 的锁最多 5s；仍忙则返回 HTTP 429 / `LANE_BUSY`，明确说明等待时间和“本次请求未执行”，并写入 archive。不同 lane 仍按 `max_concurrent_chats` 并发。例外是微信同一入站消息拆出的 metadata-less 图片分片，它继续沿用既有 lane 继承与排队行为，不能被误判成独立追问。
- **`/新对话` 是抢占控制指令**：它先使旧一代排队请求失效，再取消当前 in-flight task、重建该 lane 的 tab。被取消任务以 `REQUEST_CANCELLED` 归档；旧排队请求醒来后只返回 `LANE_BUSY`，不得调用 ChatGPT。

## 症状表

| 症状 | 先查 | 处置 |
|---|---|---|
| 开机后一直不回复 | noVNC 看 Chrome 是否卡「恢复页面」提示 | 人工关浏览器 → 自动重开干净 Chrome 即自愈；勿自动登录 |
| 回复半截/只有开场白 | detector 完成判定是否被改动 | 见上方 stop 按钮红线 |
| RESPONSE_TIMEOUT | 先看失败卡片的取证行（错误码/耗时/快照/设备）；耗时落在 ~173-190s 且请求是"生成文件"= idle 判定；落在 ~320s = failover-proxy 上限 | 长思考等即可；查 `logs/debug/<快照>/selector_report.json`，`STOP_BUTTON: true` 说明当时页面还在生成 |
| 要求发文件却只回一个文件名 | 存档 `outbound.text` 有无 `FILE:` 标记 | 无标记=没提取到/没下载成功，查 api.log 的 `file pill click did not produce a download`；有标记=bridge 侧投递问题 |
| Cloudflare 无限验证循环 | 自动化是否 attach 着 | detach 后人工过验证 |
| 多图请求后全线卡死 | 单 worker 被堵（142-153s/13图）；healthz 假绿 | 等释放或重启容器；车道隔离测试须测对车道 |
| 同群后续消息很快收到 `LANE_BUSY` | archive 查同一 `lane.key` 的 active 请求和被拒请求 | 这是车道保护：被拒消息没有发进 ChatGPT。等待当前任务完成，或发送 `/新对话` 抢占重建 |
| webdock2 整机失联 | WSL 是否活：容器 Up 时长 < 命令年龄 = 假活 | 保活任务已改开机+S4U+`wsl sleep infinity` 常驻（07-12） |

## 发送前耗时：看 `send_stages`

每次发送后 `api.log` 打一行，把"请求到达 → 文字进输入框"拆开：

```
send_stages total=2.39s login=0.19 flyout=0.01 input=0.01 mode=0.03 snapshot=0.09 type_delay=0.96 paste=0.20 send_btn=0.01 click=0.90
```

- 稳态基线（页面已热）：`total≈2.4s`，其中拟人延迟 `type_delay`+`click` 约 1.8s 是有意的反自动化特征，**不要为了提速去压**。
- 冷页面（`lane ready` 里 `page=` 有秒数，新开会话）：`total≈4.7s`，多出来的主要在 `mode` 和 `input`——composer 比输入框还晚渲染，等它是应该的。
- ⚠️ `mode` 曾经稳定占 6.02s：`ensure_mode` 把三个带文案的候选逐个探、每个各等满 2s，而多数时候只是确认模式已经对了。现在只问无文案的胶囊本体（`MODE_PICKER_BUTTON_ANY`）一次，命中即返回（07-28，`cdca628`）。
- ⛔ 那次超时预算不能再往下压：一度压到 2s，新开会话时胶囊来不及渲染 → `mode_switch_failed stage=button`，**模式静默没切成**。这比慢几秒严重得多，6000ms 是"等页面"的余量而不是浪费。

## 排障工具

- CDP 旁路：patchright `connect_over_cdp` 容器 `:9222` dump DOM。**⚠️ 9222 是 ChatGPT 生产实例别乱动**（webdock2 上 9223 是另一独立 Chrome）。
- 存档：`/var/log/webdock/archive/<UTC日期>.jsonl`，查 `status` / `outbound.chars`。
- webdock2 执行 Linux 命令：`ssh webdock2` 进的是 PowerShell，须 `wsl -d Ubuntu-24.04-WebDock -- <cmd>`；复杂 PS 用 `-EncodedCommand`。

## 超时三层与异步 job（改任何一层前先读完这节）

同步 `/v1/chat/completions` 仍要穿过三个独立上限，**最小的那个说了算**：

| 层 | 位置 | 值 | 备注 |
|---|---|---|---|
| bridge → proxy | `openclaw_bridge.py::webdock_timeout()` | 1260s | 最宽松，几乎不触发 |
| failover-proxy | 当前 business-cn 主机的受管 failover-proxy（位置查 fleet/infra） | **320s/单次 HTTP** | 同步调用的真实天花板 |
| WebDock hard cap | `runtime.json::request_hard_cap_seconds` | 1200s（07-27 前是代码默认 310s） | 浏览器后台任务的真实上限 |

- ⚠️ `response_hard_timeout_seconds` 看着像总上限，其实**从未生效**：scheduler 总是传 `max(effective_timeout, request_hard_cap_seconds)`，只有它不传时那个值才会被用到。要改上限就改 `request_hard_cap_seconds`。
- 生产 bridge 使用异步 job：`POST /v1/chat/jobs` 立即返回 `job_id`，浏览器任务在 WebDock 后台继续；bridge 用 `GET /v1/chat/jobs/{job_id}` 做短轮询。因此 320s 只约束每次提交/查询，不再截断 1200s 浏览器任务，也不用修改 failover-proxy 的 320s。
- job 按 `X-Request-ID` 幂等；同 ID 不同 payload 返回 409 `REQUEST_ID_CONFLICT`。状态为 `queued/running/succeeded/failed/cancelled`，失败保留原 `error_code/message`。1200s 从提交即开始覆盖排队和执行全过程；活动任务最多 100 个，满载返回 429 `JOB_QUEUE_FULL`；完成记录保留 24h，最多 1000 条。
- job 是 node-local：bridge 必须根据提交响应的 `X-Webdock-Route` 固定轮询最初接单的 primary/standby；不能在任务中途随主备恢复切到另一台查询。
- 同步接口保留给本地调试和旧 bridge 兼容；新 bridge 遇到旧 WebDock 的 job endpoint 404/405 才回退同步调用。
- 实测参考：生成一份 3 页 Word ≈ 289s（`Worked for 4m 49s`）；异步 job 下可继续运行并由飞书处理卡片报告等待时间。

## 部署

- 换镜像：在 infra `secrets/webdock<N>.enc.env` 更新 `WEBDOCK_IMAGE`，推送并在设备执行 `render.sh webdock<N>` + restart。`.env` 是渲染产物，禁止直接长期修改；机型化 unit 从同一渲染流程生成。
- `webdock.service` 已加 `ExecStartPre=-pull`（自愈拉镜像）；新机装 `install-ubuntu.sh` 自带。
- ⚠️ CI 只在 PR 跑 pytest，直推 main 不跑 → 直推前必须本地 `pytest -q`。
- ⚠️ Windows 侧写 JSON 必须显式 utf-8，否则 API 吃坏 body。
