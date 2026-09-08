# AI 额度监控运行说明

> 采集器源码已迁移到公开仓库
> [`huozao/ai-quota-monitor`](https://github.com/huozao/ai-quota-monitor)。本文保留
> WebDock 集成、生产验证和通知契约；不要在本仓库重新添加采集器源码。

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
- 历史采集按 `QUOTA_RETENTION_DAYS` 清理，默认保留 7 天；每轮采集后删除过期的
  `captures` 记录及其对应截图。`quota_events` / `quota_meta` 只保存去重和日报状态，
  体积很小，不随采集明细增长。
- console 卡片分别展示 5 小时限额、周限额、剩余/已用、绝对重置时间和倒计时；
  截图是解析异常时的最后证据。
- Codex 页面若没有单独提供 5 小时重置时间且剩余为 100%，展示“无需等待（额度充足）”，
  不从周额度时间推断 5 小时重置时间；周额度已耗尽时改展示“等待周额度重置后才能使用”。

### 重置时间按小节锚定，不按出现顺序

`claude_reset_sections` / `codex_reset_sections` 先用小节标题把页面文字切开
（Claude：`Current session` / `Weekly limits` / `Usage credits`；Codex：`5 hour usage limit` /
`Weekly usage limit` / `Credits remaining`），再在各自的块里找 `Resets …`。


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


`QUOTA_DISPLAY_TZ`（默认 `Asia/Singapore`）决定卡片文案和**报表时刻按哪个时区判**，
`QUOTA_TZ_LABEL`（默认 `SGT`）只是副标题里那个括号。两者都不参与额度判定——判定一律用
归一化后的绝对时间。

⚠️ **`QUOTA_REPORT_TIMES` 的三档必须按展示时区判，不能按容器本地时间。** 该写法自
2026-09-07 起改正：此前取 `datetime.now().astimezone()`，容器没设 `TZ` 就是 UTC，
于是 `08:00,13:00,20:00` 实际落在 **16:00 / 21:00 / 04:00 (SGT)**——文档写的「早/中/晚」，
收到的却是下午、深夜和凌晨（`notify_outbox` 里 `quota:daily_report:2026-09-06:20:00`
那条是次日 04:21 才发出的）。判据抽在 `pick_report_slot`，`now` 必须带展示时区。


## 观察位：X @thsottiaux（2026-09-08 起）

Codex 的重置常在这个账号先放出来，比额度页早。观察位与两个额度页**共用同一轮采集**，
provider 名是 `x-<账号>`，页面文字、帖子列表和截图一起进 `captures`，沿用同一套 7 天保留。

- 帖子逐条取自 `article[data-testid='tweet']`。⚠️ **不要从 body 文本里切**：时间线是虚拟
  列表，正文、转发说明、引用卡片和「显示更多」在纯文本里连成一片，既切不出边界也拿不到
  永久链接。
- 去重键是 **status id**，不是整条 URL——同一条帖子的链接会带 `/photo/1`、`/analytics`
  等后缀，按 URL 去重会重复推送。事件落在 `quota_events`，采集明细过期被清也不会重推。
- 推送口径：命中 `QUOTA_X_KEYWORDS`（默认 `reset,limit,quota,credit`）**且**在
  `QUOTA_X_MAX_AGE_HOURS`（默认 24）内的新帖才发飞书。⚠️ 缺年龄闸门时首次上线会把整条
  时间线的历史帖一次性推出来。不命中的帖子照样入库，在 console 卡片上看得到。
- `QUOTA_X_ACCOUNT` 置空即关闭该采集；这三个键都有代码默认值，compose 里不需要显式给。
- 标签匹配对观察位单独写（`_find_page`）：provider 名里的单字母 `x` 会命中任何含 x 的 URL，
  观察位按 `x.com` 域名找已开着的标签页，找不到才新开。
- 日报里观察位是**单独一行**，不进 `_provider_segments`（那是 5h+周额度两格排版，
  传一条没有额度字段的记录进去会渲染成两格「暂无数据」）。24h 内没有命中时也会写一句
  「无重置相关动态」——完全不渲染的话，采集挂了在日报上看不出来。
- console 第三张卡片只渲染**最新一次采集**里的帖子：每轮采集都会重复同样几条，按采集
  堆叠会把同一条帖子刷成几十遍。

## 通知规则

- 早/中/晚日报照常发送。⚠️ **「本日已发到哪一档」落在 sqlite 的 `quota_meta` 表**，不是
  进程内变量。放进程内时容器每重启一次就把当天最近一档补发一遍——2026-09-07 因为连续换
  镜像，同一档补发了好几次。判据：重启容器后查
  `SELECT value FROM quota_meta WHERE key='last_daily_report'`，再看 `notify_outbox`
  有没有多出同 dedup_key 的行。
- `quota.reset` **只由周额度变化触发**；5 小时窗口恢复不发送飞书通知。
- `quota.x_post` 一条帖子一张卡，`dedup_key` 用 status id；正文按 300 字截断，看全文点原帖。
- 周额度判定使用 `weekly_remaining` / `weekly_reset_at`，要求前后均为健康采集，
  并通过 `quota_events` 去重。
- ⚠️ **重置时间字符串变了不足以判重置**，必须同时看到剩余额度回升。2026-09-07 因为上面那个
  错位，`weekly_reset_at` 从 "Sat 10:00 AM" 变成 "Oct 1"，而 `weekly_remaining` 全程 75%
  没动，照样发出一条「周额度已重置」。判据现在落在剩余额度上（`weekly_reset_candidate`）。


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


## 已知故障与修复

- 若 noVNC 页面显示“无法连接到服务器”，先看 quota 容器内的 `x11vnc.log`。
- Xvfb 与 x11vnc 必须有启动等待：直接并行启动会出现 `XOpenDisplay(":101") failed`，
  随后 websockify 反复报 `localhost:5902 connection refused`。当前 entrypoint 会等待
  `/tmp/.X11-unix/X101` 就绪后才启动 x11vnc。

### 观察位读到 0 条帖子：时间线还没渲染（2026-09-08）

`x-<账号>` 采到 `schema_changed`、看板显示「未读到帖子」、日报挂「采集异常」，但
`captures.text` 里资料头、关注数、`Posts/Replies` 标签都在——**登录态是好的**，只是 X 的
虚拟列表还没渲染就被读了（`QUOTA_PAGE_SETTLE_SECONDS` 默认 5 秒对它不够）。判据就是读
`captures.text`：有资料头没帖子 = 渲染没跟上；整页是登录墙 = 登录态失效。
现在会先等第一条 `article` 出现（`QUOTA_X_POST_WAIT_SECONDS`，默认 15 秒）再读。

### 截图超时：静止页面的渲染器不产帧（2026-09-08）

现象：console 的 codex 那一列全是碎图，状态写着 `network_error`，而数值明明是对的。
`captures.error` 里是 `TimeoutError: Page.screenshot: Timeout 30000ms exceeded`，
call log 停在 `fonts loaded`。当天 09:04 UTC 起连续 8 轮全挂，claude 侧毫发无损。

⚠️ **`page.reload()` 救不回来**——每轮采集都先 reload 再截图，照样超时。判据取自现场探针：
codex 页 `document.getAnimations()` 为 0，空闲后第一次截图必超时；先做一次
`bring_to_front` + 指针移动 + 1px 滚动回滚，同一张图 0.1s 就返回，之后连续成功。
claude 页有 5 个常驻动画一直在产帧，所以从没触发过。触发点是当天有人在这个浏览器里
新开了标签并切走——**这个环境下四个标签的 `document.visibilityState` 全是 `visible`**
（Xvfb 无窗口管理器），所以从页面侧看不出谁在前台，别拿 visibilityState 当判据。

修复在采集器侧（`_force_repaint` / `_screenshot`）：截图前强制产帧、超时 15s、失败重试一次。
⚠️ 更要紧的是**截图不再与解析共用一个 `try`**：此前截图超时会把整条采集写成
`network_error`，而 `weekly_reset_candidate` 要求前后两次都 healthy——**codex 的周额度
重置告警因此被静默停用**，看板和日报上只表现为「网络错误」。截图失败现在只记
`screenshot_error`，`screenshot_path` 存 NULL，`screenshot_url` 只在文件真实存在时下发。

## 发布

quota-monitor 随独立仓库 `huozao/ai-quota-monitor` 的 `main` 不可变镜像发布；生产 compose
不再绑定本地源码，设备只运行 GitHub Actions 构建出的镜像版本。

完整一轮（2026-09-07 走过六次，每一步都有判据）：

1. `ai-quota-monitor` 直推 `main`（**推前本地 `pytest` 必须过**，本仓 CI 不拦直推）。
2. 等 **release** run 出镜像。⚠️ 同一个 commit 会同时触发 `ci` 和 `release` 两个 run，
   `gh run list --limit 1` 常常给的是 `ci`——它只有 `test` 一个 job，绿了也**没有镜像**。
   判据：`gh run view <id> --json jobs` 里必须看到 `build-push: success`。
   ⚠️ 构建耗时**别按 9 分钟估**：Dockerfile 的 apt 源指向 `mirrors.aliyun.com`，
   GitHub runner 从境外拉这个镜像站很慢，workflow 也没配 layer cache，
   2026-09-08 那次跑了 30 分钟以上。
3. `sops set secrets/webdock2.enc.env '["QUOTA_IMAGE"]' '"ghcr.io/huozao/ai-quota-monitor:sha-<完整40位>"'`。
   ⚠️ 用完整 SHA，别照着短 SHA 手拼——拼错了会 pin 到一个不存在的 tag。
   （webdock 主容器的镜像是另一个键 `WEBDOCK_IMAGE`，别改错。）
4. infra 推 origin + 三个 device bare，然后设备上
   `sudo -u webdock git -C /home/webdock/infra pull --ff-only origin main`
   （webdock2 已改直拉 GitHub，见 `infra/roles/webdock/README.md`〈设备直拉 GitHub〉）。
5. `sudo /home/webdock/infra/scripts/render.sh webdock2` → 只应看到
   `UPDATED: /opt/webdock/deploy/laptop/.env`。
6. `docker compose -p webdock --env-file … pull quota-monitor` 后
   `up -d --no-build quota-monitor`——**只重建这一个容器，别动 webdock 主容器**。
7. 判据三件套：`docker inspect --format '{{.Config.Image}}' quota-monitor` 的 tag 变了、
   容器内 `grep` 到本次新增的函数名、等一轮采集看 `captures` 里新行的字段。

⚠️ **不要用命令行临时传 env 起这个容器。** 2026-09-08 实测的后果有三层，且全都静默：
`QUOTA_IMAGE` 不在 sops 里时 compose 默认退回 `:latest`（跑的是哪个 commit 说不清）；
`NOTIFY_ENDPOINT` 漏了就是**飞书日报和重置告警全部停发**——`_notify()` 端点为空直接
return，而 `quota_meta.last_daily_report` 照样落库，那一档**不会补发**；
`QUOTA_PUBLIC_LINK` / `QUOTA_PUBLIC_API_PREFIX` 漏了则卡片链接为空、看板截图 404。
这三个键自 2026-09-08 起分别落在 sops（`QUOTA_IMAGE`）和 `deploy/laptop/compose.yml`
（其余两个有默认值），照上面的 `--env-file` 路径重建即可，判据是重建后
`docker inspect` 里这三个 env 都非空。

⚠️ **跨仓发版顺序**：卡片用到中枢的新字段时（如 `NotifyField.note`），必须
**AliECS backend 先上线**。旧模型对多余字段是 pydantic 默认的静默忽略，先上 webdock
的后果不是「样式没生效」而是**那一行整个消失**（2026-09-07 在生产容器里实测过）。

## 交接快照（2026-09-08 晚）

⚠️ **这一节是时间点快照，判据只在写下的那一刻成立**；接手时先跑下面〈交接时先核验〉那三条，
以现场为准，不要拿本节的 SHA 和条数当现状。

- 采集器仓 `huozao/ai-quota-monitor` 已迁进工作区
  `~/src/AliECS-WebDock/ai-quota-monitor`（旧路径 `~/src/ai-quota-monitor` 留了兼容软链）。
  当天 main=`65f4175`。
- **生产镜像 pin 在 `sha-031cdfba…`，不是 main 头**——031cdfb 之后的提交只动 CI/测试/文档，
  没有运行时变化，故意没发。下次功能改动一并带上即可。
- 采集对象**三个**：`codex`、`claude`，以及观察位 `x-thsottiaux`（X 时间线，重置预告）。
  provider 名进了 API、看板和 `quota_events`，加减观察位要一起看这三处。
- 当天现场：249 条采集（2026-09-06 起），`quota_events` 12 条，
  `quota_meta.last_daily_report=2026-09-08:20:00`。卷仍是
  `/var/lib/webdock/quota_{browser_data,data}` 与 `/var/log/webdock/quota-monitor`。
- 当天修掉的三件事，判据都在前面对应小节：截图超时（静止页面不产帧）、
  `NOTIFY_ENDPOINT` 被临时命令行起容器时清空导致**日报和告警静默停发**、
  X 时间线未渲染就读被误判成 `schema_changed`。
- `QUOTA_IMAGE` 自当天起由 sops `secrets/webdock2.enc.env` 管理，`render.sh webdock2`
  渲染进 `/opt/webdock/deploy/laptop/.env`；`QUOTA_PUBLIC_API_PREFIX` /
  `QUOTA_PUBLIC_LINK` 在 `deploy/laptop/compose.yml` 里有默认值。
- 观察位轮询**沿用 20–30 分钟主循环**（当天明确决定不单独提速）：新帖最坏 30 分钟后被发现。
- console 两页分工见 `infra/console/README.md`〈两个页面的分工〉：实时卡片和历史都在
  `/console/quota/`，`/console/` 只留入口且不再有脚本。
- `QUOTA_RETENTION_DAYS=7` 生效；清理在每轮采集后执行，删除过期 `captures` 行及其截图，
  不动浏览器 profile、`quota_events` 和 `quota_meta`。
- `/console/quota/` 曾因 txecs 静态页 `root:root 0640` 返回 403；现为 `root:www-data 0640`
  （infra `f00b69b`）。重新渲染依赖 `roles/server/tencent/apply.sh` 里的 `chown root:www-data`。
- webdock2 的 `/opt/webdock` 是**无 Git 的部署副本**，用仓库 raw 文件定点更新，
  不要对它 git pull/reset。

### 交接时先核验

```bash
ssh webdock2 "wsl -d Ubuntu-24.04-WebDock -- docker inspect quota-monitor --format '{{.Config.Image}} {{.State.Health.Status}}'"
ssh webdock2 "wsl -d Ubuntu-24.04-WebDock -- docker exec quota-monitor python -c 'import sqlite3; c=sqlite3.connect(\"/app/quota_data/quota.sqlite3\"); print(c.execute(\"select count(*),min(captured_at),max(captured_at) from captures\").fetchone())'"
ssh txecs "sudo stat -c '%A %a %U:%G %n' /usr/local/share/site-entry/console-quota.html"
# 三个 provider 各自最近一次采集的状态（观察位算第三个）
ssh webdock2 "wsl -d Ubuntu-24.04-WebDock -- docker exec quota-monitor python -c 'import sqlite3; c=sqlite3.connect(\"/app/quota_data/quota.sqlite3\"); [print(r) for r in c.execute(\"select provider,max(captured_at),status from captures group by provider\")]'"
```

认证页面的无 cookie `curl` 只能证明 Authelia 302；要证明用户界面，需在已登录浏览器
中打开 `/console/quota/`，或核对 nginx access log 中的 200。不要把真实 SQLite、截图、
浏览器 profile、Token 或生产 `.env` 放入公开仓库。
