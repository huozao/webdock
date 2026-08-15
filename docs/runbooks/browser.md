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
- **图改图的完成判据只有一个能信：imagegen scaffold 里有没有落地的图**（`imagegen_pending`）。2026-08-14 诊断探针实测一条 43s 的图改图，逐秒取证结论如下，改这块前先对着看：
  - SSE `[DONE]` 在 4.2s、WebSocket `finished_successfully` 在 6.1s 就到，**图片 36.8s 才进 DOM**。协议终态说的是"服务器写完了"，不是"页面画完了"，早 30 秒，不能当完成判据。
  - 图片渲染的这 30 秒里 `stop_present` **全程为 true**。07-17 记的"stop 按钮会 flap"不是常态，别当成必然；但也别反过来当保证。
  - 闪烁/动画组件（`role=status`、`loading-shimmer`）**比 stop 先灭，中途还有空窗**：35.6s 那一帧 scaffold 在、图为 0、`animated_candidates` 已经空了。所以它挡不住这个窗口，`completion_ready()` 里刻意没有它，别再往回加。
  - 生成图 src 会 **1 → 0 → 1 跳变**（ChatGPT 换图重渲），detector 返回后紧接着扫一次可能正好扑空 → `chatgpt_page` 用 `IMAGE_RESCAN_SECONDS` 有界重扫补回。
  - imagegen 覆盖层的 `Edit`/`Share` 会混进 `rich_assistant_text`（它们不是 button，逃过 UI 过滤），所以**不要用文字判完成**，图画完了 `Edit` 照样在。
  - 复现工具：`runtime.json` 临时置 `diagnostic_probe_enabled=true`，请求带 `X-Webdock-Probe-ID`，逐秒 `dom_state` 落在 `/app/logs/probes/<probe-id>.jsonl`，跑完改回 false。

<!-- nav-check-python: src/browser/detector.py:imagegen_pending -->
<!-- nav-check-python: src/browser/detector.py:rich_assistant_text -->
<!-- nav-check-python: src/browser/chatgpt_page.py:IMAGE_RESCAN_SECONDS -->
<!-- nav-check-python: src/browser/response_lifecycle_probe.py:completion_ready -->
- OpenClaw monitor 串行投递图片（慢是设计不是 bug）；⛔ bridge 反转合并别重试。
- 文件附件：捕获正则必须容忍 ` (image/*)`；context-summary 历史块要先剥离防死循环。
- **生成文档 pill 点击 = 开预览飞出层，不触发 download**（07-27）。层是 `data-testid=stage-thread-flyout` / `screen-threadFlyOut`，自带 `aria-label=Download`；**Escape 关不掉它**（实测 width 751 扛过多次 Escape），必须点 `data-testid=close-button`。层不关会盖住会话，下一轮永远等不到完成信号 → 整条 lane 被 wedge。每轮发送前会清一次残留层。
- **idle 判定必须尊重 stop 按钮**（07-27）。ChatGPT 跑代码生成文档时页面完全静止（文本冻结、stop 亮着），progress signature 不变；旧逻辑在 `soft_deadline + idle_timeout` 处判死，实测 173/173/188/191s 全灭，而页面本身 4m49s 才生成完。
- **ChatGPT 自己的失败横幅**（`Something went wrong while generating the response` + Retry）现由 `generation_error_text()` 识别，立即报 `GENERATION_FAILED`。只认最后一轮的横幅——历史失败轮会永远留在会话里，匹配到就会毒化之后每个请求。
- **同车道不再长时间排队**：普通请求等待同一 `lane.key` 的锁最多 5s；仍忙则返回 HTTP 429 / `LANE_BUSY`，明确说明等待时间和“本次请求未执行”，并写入 archive。不同 lane 仍按 `max_concurrent_chats` 并发。例外是微信同一入站消息拆出的 metadata-less 图片分片，它继续沿用既有 lane 继承与排队行为，不能被误判成独立追问。
- **`/新对话` 是抢占控制指令**：它先使旧一代排队请求失效，再取消当前 in-flight task、重建该 lane 的 tab。被取消任务以 `REQUEST_CANCELLED` 归档；旧排队请求醒来后只返回 `LANE_BUSY`，不得调用 ChatGPT。

<!-- nav-check-python: src/browser/detector.py:generation_error_text -->
<!-- nav-check-python: src/utils/errors.py:GENERATION_FAILED -->

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

<!-- nav-check-python: src/browser/chatgpt_page.py:ensure_mode -->
<!-- nav-check-python: src/browser/selectors.py:MODE_PICKER_BUTTON_ANY -->

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
| WebDock hard cap | `runtime.json::request_hard_cap_seconds` | 1200s（代码默认 2026-08-15 起也是 1200） | 浏览器后台任务的真实上限 |

⚠️ **上表这行的旧写法「1200s（07-27 前是代码默认 310s）」自 2026-08-15 起确认会误导**：它读起来像"310 已经是历史"，
实际 07-27 只改了设备上的 `runtime.json`，代码默认一直停在 310——webdock1 因此静默跑了三周 310s。
2026-08-15 已把 `config.py` 与 `lane_scheduler.py` 的默认一并抬到 1200，根因消除；机制和缺键语义见下方
「runtime.json：host 权威，改完必须重启」节。**判断生产行为仍要读设备文件，别只看代码默认**——这次是两者恰好一致了，不是这条规矩失效了。

- ⚠️ `response_hard_timeout_seconds` 看着像总上限，其实**从未生效**：scheduler 总是传 `max(effective_timeout, request_hard_cap_seconds)`，只有它不传时那个值才会被用到。要改上限就改 `request_hard_cap_seconds`。
- 生产 bridge 使用异步 job：`POST /v1/chat/jobs` 立即返回 `job_id`，浏览器任务在 WebDock 后台继续；bridge 用 `GET /v1/chat/jobs/{job_id}` 做短轮询。因此 320s 只约束每次提交/查询，不再截断 1200s 浏览器任务，也不用修改 failover-proxy 的 320s。
- job 按 `X-Request-ID` 幂等；同 ID 不同 payload 返回 409 `REQUEST_ID_CONFLICT`。状态为 `queued/running/succeeded/failed/cancelled`，失败保留原 `error_code/message`。1200s 从提交即开始覆盖排队和执行全过程；活动任务最多 100 个，满载返回 429 `JOB_QUEUE_FULL`；完成记录保留 24h，最多 1000 条。
- job 查询响应的 `progress` 是可复用的脱敏生命周期接口（`schema_version=1`）：只提供 `phase`、耗时及 Stop/状态组件/操作区/服务器终止等布尔信号，不含提示词、回复正文、DOM 或网络载荷。bridge/卡片只消费这个接口，不直接依赖 detector 内部实现。
- 普通请求默认只复用 detector 已经读取的 DOM 信号，**不会**额外创建 CDP Network 会话或扫描状态组件。仅显式诊断请求（运行时 `diagnostic_probe_enabled=true` 且带 `X-Webdock-Probe-ID`）会记录脱敏 JSONL；普通请求的被动协议监听还需单独在 `runtime.json` 显式设 `lifecycle_network_monitor_enabled=true`。⚠️ **代码默认 `false`，但 webdock2 生产实际是 `true`**（2026-08-14 核对）——判断"这条路径生产上跑不跑得到"要去读设备上的 `runtime.json`，别按代码默认推。
- 协议完成只能作为组合证据，而且**排在 `in_progress` 之后**：本次任务进入过生成态 + task-correlated WebSocket terminal + Stop 消失 + 操作区出现，且 imagegen scaffold / image-gen 占位 / interim 状态文字都不在。SSE `[DONE]` 早于页面完成，不能单独判终局；未开启协议监听时继续走原 DOM/text 兜底。
  - ⚠️ 2026-08-12 引入这条快通道时它排在 `in_progress` **之前**，等于绕开 07-18 为图改图加的 scaffold 闸门，08-14 生产上回归成"只回 Edit、图丢失"。2026-08-14 已改为 `not in_progress` 前置；连带的行为变更：协议终态**不再**压过 interim 状态文字。
- reload 兜底（页面卡死 / stop 按钮卡住时刷新页面重试）**至今没有实现**。它只出现在 `docs/superpowers/specs/2026-08-12-webdock-response-lifecycle-probe-design.md` 里，且那份 spec 明确把它排除在该阶段之外（"不在本阶段实现 reload 探针、提前判死或新的超时策略"）。别把它当成现存机制。
- **闪烁组件（动态状态组件）已进完成判定**（08-14）：`response_lifecycle_status_component()` 为 True 时算 `in_progress`，页面自己说"还在干活"就不许判完成。它是 stop 按钮 flap 掉时的第二道保险——那次 flap 正是"只回 Edit、图丢失"的直接窗口。
  - 实测依据（08-14 两条诊断样本）：271.8s 的长文档任务里 shimmer 从 2.7s 一直亮到 243.5s，**"跑代码、页面完全静止"那段照样亮着**；图改图那条也是全程亮。它熄灭到 stop 熄灭的间隔分别是 4.9s 和 1.3s。
  - ⚠️ 反向风险已加界：动画节点残留（页面其实完事了但节点没卸载）会把整轮拖到 1200s 硬顶，所以复用 `STUCK_GRACE_SECONDS` —— 没有别的生成信号且文本稳定超过宽限后，残留的动画不再算"在忙"。改这个判据前先想清楚这两个方向。
  - 采不到结构时（没人采样 DOM structure）返回 `None`，**必须当"未知"而不是"组件不在"**，否则关掉监听会变成"页面停止工作"。
  - 卡死形状（shimmer 灭、stop 却一直亮）没有做自动处置：用户在使用中发现异常直接报，再定 reload 方案。

<!-- nav-check-python: src/browser/detector.py:STUCK_GRACE_SECONDS -->
<!-- nav-check-python: src/browser/response_lifecycle_probe.py:response_lifecycle_status_component -->
- job 是 node-local：bridge 必须根据提交响应的 `X-Webdock-Route` 固定轮询最初接单的 primary/standby；不能在任务中途随主备恢复切到另一台查询。
- 同步接口保留给本地调试和旧 bridge 兼容；新 bridge 遇到旧 WebDock 的 job endpoint 404/405 才回退同步调用。
- 实测参考：生成一份 3 页 Word ≈ 289s（`Worked for 4m 49s`）；异步 job 下可继续运行并由飞书处理卡片报告等待时间。

<!-- nav-check-python: src/config.py:response_hard_timeout_seconds -->
<!-- nav-check-python: src/config.py:request_hard_cap_seconds -->

## 部署

- 换镜像：在 infra `secrets/webdock<N>.enc.env` 更新 `WEBDOCK_IMAGE`，推送并在设备执行 `render.sh webdock<N>` + restart。`.env` 是渲染产物，禁止直接长期修改；机型化 unit 从同一渲染流程生成。
- 设备侧两条命令就够（2026-08-14 在 webdock2 实跑）：

```bash
# webdock1：ssh 直接是 Linux。⚠️ 必须显式传 key 路径，见下条
sudo SOPS_AGE_KEY_FILE=/home/webdock/.config/sops/age/keys.txt /home/webdock/infra/scripts/render.sh webdock1
sudo systemctl restart webdock
# webdock2：Windows 宿主，须穿 WSL；两条分开发，别拼 &&
ssh webdock2 "wsl -d Ubuntu-24.04-WebDock -- sudo /home/webdock/infra/scripts/render.sh webdock2"
ssh webdock2 "wsl -d Ubuntu-24.04-WebDock -- sudo systemctl restart webdock"
```

- render 必须 **root** 跑（要写 `/opt/webdock/deploy/laptop/.env`），但 **age key 的位置两机不一样**（2026-08-15 实测）：webdock2 的 root 有 `/root/.config/sops/age/keys.txt`，直接 sudo 即可；**webdock1 的 key 只在 `/home/webdock/.config/sops/age/keys.txt`，root 下没有**，不显式传 `SOPS_AGE_KEY_FILE` 就会以 `no master key was able to decrypt the file` 失败——这不是密钥坏了，是 sudo 后 `$HOME` 变成 `/root` 而那儿没有 key。
- ⚠️ 但 **git pull 必须用 `webdock` 用户**（root pull 会写出 root 属主对象，下次同步卡在半更新态）——和上一条别混。
- webdock2 同步 infra **不经过设备上那个 bare**（它接不了 push、长期落后，原因见 `infra/AGENTS.md`「webdock2 同步链路」）。bundle 直喂工作树，一条命令：

```bash
sudo -u webdock git -C /home/webdock/infra pull --ff-only /mnt/c/temp/<file>.bundle main
```
- 验证顺序：设备 `docker ps` 看 tag 变新 + healthy → 从当前 business-cn 主机 `curl -i http://127.0.0.1:11800/healthz`，看 `X-Webdock-Device` / `X-Webdock-Route` 是否还是预期主机（重启不该改变主备，若变了说明 failover 切走了）。
- ⛔ restart 会**重建容器、Chrome 随之重启**，中断生产链路 1-2 分钟（登录态在 `browser_data` 卷不会丢）。动手前先与用户确认时机。
- `webdock.service` 已加 `ExecStartPre=-pull`（自愈拉镜像）；新机装 `install-ubuntu.sh` 自带。**webdock2 出网可达 ghcr**（2026-08-14 实测），换镜像不需要从 devbox 递镜像；要走 bundle 的只有 infra 仓本身，见 `infra/AGENTS.md`「webdock2 同步链路」。
- ⚠️ CI 只在 PR 跑 pytest，直推 main 不跑 → 直推前必须本地 `pytest -q`。
- ⚠️ Windows 侧写 JSON 必须显式 utf-8，否则 API 吃坏 body。

## runtime.json：host 权威，改完必须重启

- 生效点是 `browser_data/runtime.json`，**进程启动时读一次**（`config.py`），热改不生效，必须 `systemctl restart webdock`。
- `render.sh` 对它只做镜像比对：`MIRROR DRIFT` 是**告警不是失败**，脚本不会覆盖，host 才是权威。仓库 `config/webdock/runtime.json` 只是新机基线。
- ⚠️ **缺键 = 静默落回下一层，不报错也不打日志**。取值链是 dataclass 默认（`config.py` 的 `request_hard_cap_seconds`）→ 环境变量（`REQUEST_HARD_CAP_SECONDS`，2026-08-15 实测**两机 `.env` 都没设**）→ `runtime.json` override，而 override 的实现是 `if field in data` 才覆盖。所以键不在就一路落到 dataclass 默认。这条机制对所有 runtime 键都成立，不因某个键的默认值改了而消失。
- 2026-08-14 实测 webdock1 缺这个键，硬顶 310s 而 webdock2 是 1200s：**同一个镜像、两台机行为差 4 倍，日志里没有任何一行提示**。已补齐对齐。`media_base_url` 当时也还停在旧公网域名，一并改成内网值。排"备机行为和主机不一样"先 diff 两边的 runtime.json。
- 这个数管的是**墙钟绝对上限**（`lane_scheduler.py` 的 `hard_cap = max(effective_timeout, request_hard_cap_seconds)`）：超过就取消整个 task 返回 RESPONSE_TIMEOUT，ChatGPT 正在正常输出也照砍。`chat_timeout_seconds`(120s) 是软超时，判的是页面 idle，两者不是一回事。
- **310 的由来别当成随手取的数**：它是同步接口时代贴着 failover-proxy 的 320s/单次 HTTP 设的——浏览器端比上游先失败，才能返回结构化错误码而不是被代理掐断连接。07-27 生产改异步 job 后这个理由不再成立，设备值抬到 1200，但代码默认漏了，直到 2026-08-15 才补上（`config.py` + `lane_scheduler.py` 两处）。
- ⚠️ **抬到 1200 的代价要记住**：同步 `/v1/chat/completions` 回退路径（旧 bridge）上，浏览器端不再比 320s 的代理先失败，超时表现会从结构化 `RESPONSE_TIMEOUT` 变成被代理掐断的断流。生产 bridge 走异步 job 不受影响；哪天有人排"同步调用超时看不到错误码"，根因在这里。
- **失效场景是"只在备机接管期间出现"**：webdock1 平时不接单，缺键无感；一旦 failover 切过去，长任务（生成 Word 实测 289s）跑到 310s 被砍，切回主机又复现不了。

<!-- nav-check-python: src/config.py:request_hard_cap_seconds -->
<!-- nav-check-python: src/browser/lane_scheduler.py:DEFAULT_REQUEST_HARD_CAP_SECONDS -->

- 两机预期差异：`routing_backend_url` 目前只有 webdock2 有（指向本机 routing 后端），不是漂移。其余键两机应一致。
