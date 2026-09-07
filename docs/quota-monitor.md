# AI 额度监控运行说明

## 排障入口（先读这段）

三条链路，出问题先判断落在哪一段：

| 现象 | 落在哪 | 看本文哪节 |
|---|---|---|
| 数值本身不对 / 两个窗口的重置时间互串 | 采集（webdock2 `quota-monitor` 容器） | 〈重置时间按小节锚定〉 |
| 数值对但时间早/晚 8 小时、倒计时是 0 | 时区归一化 | 〈时区〉 |
| console 页面不显示倒计时、重置显示成 2001 年 | 看板（txecs 静态页） | 〈时区〉末段 + `infra/console/README.md` |
| 飞书卡片排版错位、内容重复、字挤成两行 | 卡片渲染（AliECS 中枢） | 〈卡片排版〉 |
| 卡片没收到 | 中枢投递，**判据看 `notify_deliveries` 不看 `notify_outbox`** | `AliECS/docs/runbooks/notify.md` |

只读取证命令（都不改状态）：

```bash
# 采集是否新鲜、字段对不对（captured_at 是 UTC）
ssh webdock2 "wsl -d Ubuntu-24.04-WebDock -- bash -lc 'docker exec quota-monitor python -c \"import sqlite3;[print(r) for r in sqlite3.connect(\\\"/app/quota_data/quota.sqlite3\\\").execute(\\\"SELECT id,provider,captured_at,status,fields_json FROM captures ORDER BY id DESC LIMIT 4\\\")]\"'"
# 看板拿到的对外字段
ssh webdock2 "wsl -d Ubuntu-24.04-WebDock -- bash -lc 'curl -sS http://127.0.0.1:18002/v1/quota/latest'"
# 发了哪些通知、有没有真送达
ssh txecs "sudo docker exec business-cn-postgres-1 psql -U app -d app -c \"select o.id,o.event,o.dedup_key,o.created_at,d.status from notify_outbox o left join notify_deliveries d on d.outbox_id=o.id where o.source_key='quota-monitor' order by o.id desc limit 8;\""
```

原始页面文字整段存在 `captures.text` 里，截图存在 `captures.screenshot_path`——**判断页面是不是
改版了，一定去读这两样，不要凭代码默认值推断**。

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

2026-09-07 上线当天又在生产上验到两种形状，都已覆盖：Codex 5 小时窗口用掉一部分后，
两个窗口的重置时间都渲染成**裸时钟**（`Resets 3:59 AM` / `Resets 2:24 AM`，无日期无星期）；
页面顶部同时多了一条 `Your limit will reset after 2:24 AM.` 的横幅——它在两个小节之外，
锚定天然排除它，而旧的全页取值正好命中它（capture id=59 的两个字段都是 "after 2:24 AM."）。
`normalize_reset` 因此要认裸时钟（按「下一次出现该时刻」解析），引导词 `in` / `at` / `after`
和句末句号在取值时剥掉。

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

`QUOTA_DISPLAY_TZ`（默认 `Asia/Singapore`）决定卡片文案和**报表时刻按哪个时区判**，
`QUOTA_TZ_LABEL`（默认 `SGT`）只是副标题里那个括号。两者都不参与额度判定——判定一律用
归一化后的绝对时间。

⚠️ **`QUOTA_REPORT_TIMES` 的三档必须按展示时区判，不能按容器本地时间。** 该写法自
2026-09-07 起改正：此前取 `datetime.now().astimezone()`，容器没设 `TZ` 就是 UTC，
于是 `08:00,13:00,20:00` 实际落在 **16:00 / 21:00 / 04:00 (SGT)**——文档写的「早/中/晚」，
收到的却是下午、深夜和凌晨（`notify_outbox` 里 `quota:daily_report:2026-09-06:20:00`
那条是次日 04:21 才发出的）。判据抽在 `pick_report_slot`，`now` 必须带展示时区。

<!-- nav-check-python: quota_monitor/core.py:pick_report_slot -->

## 通知规则

- 早/中/晚日报照常发送。⚠️ **「本日已发到哪一档」落在 sqlite 的 `quota_meta` 表**，不是
  进程内变量。放进程内时容器每重启一次就把当天最近一档补发一遍——2026-09-07 因为连续换
  镜像，同一档补发了好几次。判据：重启容器后查
  `SELECT value FROM quota_meta WHERE key='last_daily_report'`，再看 `notify_outbox`
  有没有多出同 dedup_key 的行。
- `quota.reset` **只由周额度变化触发**；5 小时窗口恢复不发送飞书通知。
- 周额度判定使用 `weekly_remaining` / `weekly_reset_at`，要求前后均为健康采集，
  并通过 `quota_events` 去重。
- ⚠️ **重置时间字符串变了不足以判重置**，必须同时看到剩余额度回升。2026-09-07 因为上面那个
  错位，`weekly_reset_at` 从 "Sat 10:00 AM" 变成 "Oct 1"，而 `weekly_remaining` 全程 75%
  没动，照样发出一条「周额度已重置」。判据现在落在剩余额度上（`weekly_reset_candidate`）。

<!-- nav-check-python: quota_monitor/core.py:weekly_reset_candidate -->

### 卡片排版

明细一律走 `segments`，`summary` 只留概述或留空。⚠️ 飞书 channel 会把 `summary` 和
`segments` **依次**渲染，同一段文字既传 `summary` 又传一个 `text` segment，卡片里就会出现
两遍——2026-09-07 的日报重复就是这么来的。

每个平台渲染成「一行标题 + 一行两格指标」（`text` + `fields`），指标格里三行：指标名、
数值、灰色副信息。

⚠️ **不要用 `section` 三列排额度**。该写法自 2026-09-07 起确认会错位：中枢的
`_section_element` 把指标名和指标值**各拼成一个 markdown 块**，两列靠「行数相同」对齐，
值一旦折行两列就整体错开——实测卡片里「Credits」对到了上一行的值上、「下次重置」对到了
空处。aliecs 流量日报不出问题是因为它的值短到不折行（`1.23 GB`），而额度这边
`85% 剩余 · 9/14 10:33（约 6天22小时后）` 在半屏宽下必然折行。`fields` 的每一格是独立
column，折行只让那一格变高，不牵动邻格；顺带每格宽度从 5/12 变成 1/2。

文案为半屏宽收窄，这几条是约定不是口味：

- 倒计时用**英文单位** `d` / `h` / `min`（`6d 21h`、`3h 47min`）。中文单位长一半，容易
  把最后一行挤到折行；分钟一律 `min` 不写 `m`——单个 m 在时间语境里会被读成 month。
  判据抽在 `countdown_label`。
- **不写「后」**。这一行的语境已经是重置倒计时，那个字纯占宽度。
- 有重置时间时前缀 `重置 `，同一天省掉日期（`重置 16:40 · 3h 47min`）。
- 指标名用 `5h` 不用 `5 小时`；顶部副标题 `截至 2026/09/07 12:52 · SGT`。
- 副信息走 `NotifyField.note` 而不是拼进 `value`。飞书 markdown **不支持行内字号**，同一个
  markdown 元素里的字只能一样大；中枢把 note 单独渲染成 `notation` 号，窄屏下正好省出那点
  宽度，`重置 9/14 10:33 · 6d 21h` 才不折行。
  ⚠️ **`note` 需要中枢先上线**：旧版 `NotifyField` 只有 name/value，pydantic 默认忽略多余
  字段，所以对着旧 backend 发 note 不是「字变大」而是**整行消失**（2026-09-07 在生产容器里
  实测过）。发版顺序必须是 AliECS backend 先、webdock 后。
- **拿不到重置时间就不写重置**：满额度显示「额度充足」，周额度耗尽时 5h 那格显示
  「等待周额度重置」，一律不从别的窗口推算时间。

<!-- nav-check-python: quota_monitor/core.py:countdown_label -->

## 已知故障与修复

- 若 noVNC 页面显示“无法连接到服务器”，先看 quota 容器内的 `x11vnc.log`。
- Xvfb 与 x11vnc 必须有启动等待：直接并行启动会出现 `XOpenDisplay(":101") failed`，
  随后 websockify 反复报 `localhost:5902 connection refused`。当前 entrypoint 会等待
  `/tmp/.X11-unix/X101` 就绪后才启动 x11vnc。

## 发布

quota-monitor 随 webdock GitHub `main` 的不可变镜像发布；生产 compose 不再绑定本地源码，
设备只运行 GitHub Actions 构建出的镜像版本。

完整一轮（2026-09-07 走过六次，每一步都有判据）：

1. webdock 直推 `main`（**推前本地 `pytest` 必须过**，本仓 CI 不拦直推）。
2. 等 **release** run 出镜像。⚠️ 同一个 commit 会同时触发 `ci` 和 `release` 两个 run，
   `gh run list --limit 1` 常常给的是 `ci`——它只有 `test` 一个 job，绿了也**没有镜像**。
   判据：`gh run view <id> --json jobs` 里必须看到 `build-push: success`
   （`mirror-to-tcr: skipped` 属常态）。
3. `sops set secrets/webdock2.enc.env '["WEBDOCK_IMAGE"]' '"ghcr.io/huozao/webdock:sha-<完整40位>"'`。
   ⚠️ 用完整 SHA，别照着短 SHA 手拼——拼错了会 pin 到一个不存在的 tag。
4. infra 推 origin + 三个 device bare，然后设备上
   `sudo -u webdock git -C /home/webdock/infra pull --ff-only origin main`
   （webdock2 已改直拉 GitHub，见 `infra/roles/webdock/README.md`〈设备直拉 GitHub〉）。
5. `sudo /home/webdock/infra/scripts/render.sh webdock2` → 只应看到
   `UPDATED: /opt/webdock/deploy/laptop/.env`。
6. `docker compose -p webdock --env-file … pull quota-monitor` 后
   `up -d --no-build quota-monitor`——**只重建这一个容器，别动 webdock 主容器**。
7. 判据三件套：`docker inspect --format '{{.Config.Image}}' quota-monitor` 的 tag 变了、
   容器内 `grep` 到本次新增的函数名、等一轮采集看 `captures` 里新行的字段。

⚠️ **跨仓发版顺序**：卡片用到中枢的新字段时（如 `NotifyField.note`），必须
**AliECS backend 先上线**。旧模型对多余字段是 pydantic 默认的静默忽略，先上 webdock
的后果不是「样式没生效」而是**那一行整个消失**（2026-09-07 在生产容器里实测过）。
