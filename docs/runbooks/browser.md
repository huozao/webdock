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
  - 复现工具：`runtime.json` 临时置 `diagnostic_probe_enabled=true`，请求带 `X-Webdock-Probe-ID`，逐秒 `dom_state` 落在 `/app/logs/probes/<probe-id>.jsonl`，跑完改回 false。⚠️ "请求带 header"这句自 2026-08-16 起不再是唯一入口：bridge 从不发这个 header，所以**照这句做只能采到自己手工构造的请求，采不到飞书真实流量**——而完成判定要看的恰恰是真实流量。现开关打开时没带 header 的请求会自动用 `auto-<request_id>` 落盘，飞书流量直接可采。

<!-- nav-check-python: src/browser/detector.py:imagegen_pending -->
<!-- nav-check-python: src/browser/detector.py:rich_assistant_text -->
<!-- nav-check-python: src/browser/chatgpt_page.py:IMAGE_RESCAN_SECONDS -->
<!-- nav-check-python: src/browser/response_lifecycle_probe.py:completion_ready -->
- **入站图片/文件的上传必须被证实，不能只看"set 完了"**（08-16）。`upload_images` 用 `set_input_files` 塞隐藏 `input[type=file]`（图片和文档同一条路，只在等待策略上分叉），但刚导航完的 composer 会**静默吞掉**这一次 set——和 `paste_text` 注释里记的 ProseMirror 静默 no-op 是同一类问题。判据是 `attachment_count()` **相对上传前变多**（不是"页面上有没有附件"：会话里的历史图也匹配 `ATTACHMENT_PREVIEW`）；没变多就重找 input 再 set 一次，仍不落地则返回 0，调用方报 `UPLOAD_FAILED` 且**不发这一轮**。
  - 实测（08-15 06:21、07:25，08-16 05:42 三次失败 + 同期成功样本）：`api.log` 里"lane ready → send_stages"的空档，**11.5-13.3s = 上传打空**（8s 检测超时 + 3s 旧兜底 sleep），**2-3s = 附件已落地**。旧代码这里无任何日志，现在每次上传都有 `upload_stages total= files= attempts= input_found= attached= url=`。
  - 三次失败全部发生在 `force_new=True`（`/新对话` 新开 tab 刚导航到 project 页），紧接着在 `/c/` 会话页重发同样的图必成功；但 08-12 全天 8 次 `/新对话` 带图全成功，所以是**新页竞态，不是新页必错**——排查时别把 force_new 当充分条件。⚠️ **"不是新页必错"这句自 2026-08-17 起确认会误导**：翻完整段 `upload_stages` 历史后事实更硬——**project 页 8 条记录全是 `attempts=2`，即第一次 set 必打空**，只是第二次通常兜住了（总耗时稳定 11.0-11.3s = 8s 检测超时 + 3s 第二次）；`/c/` 会话页 6 条全是 `attempts=1`、2.6-3.0s。08-16 之所以看着"时好时坏"，是因为当时只有失败样本进了日志。新情况见下条。
  - **project 页第一次必打空的原因是抢跑，不是网络**（08-17 实测）：临时 tab 上量 project 页 TTFB 629ms、DOMContentLoaded 1.43s，**t=1.4s 时页面上连 `input[type=file]` 都不存在**，2.8s 时编辑器、发送按钮、三个 file input 一起出现。旧代码只等"input 出现"就立刻 set，正踩在 composer 刚挂载那一瞬。
    - 现在 set 之前先 `_wait_composer_ready`：等编辑器可见 + file input 存在，**再 click 编辑器唤醒**（`paste_text` 对付 ProseMirror 静默 no-op 用的是同一招），日志多了 `ready=<秒数>/<True|False>`。
    - `_UPLOAD_ATTEMPTS` 2 → **3**。两次不是"一次重试"而是**零余量**：08-17 12:33 那次第二次也没赶上，直接 `attached=0` 报 UPLOAD_FAILED（`total=17.23s` = 8+8）。
    - `FILE_INPUT` 收窄成 composer 作用域优先、裸 `input[type='file']` 兜底。⚠️ 但 dump 证实 **project 页和会话页结构完全相同**（3 个 input 全在 composer 子树内：第一个在 `form.group/composer` 的 `div.hidden` 里、`accept=""` 通用口，另两个 `accept="image/*"`），所以**选择器从来没选错，也不存在把图传进项目文件的风险**——这条收窄是防御未来，不是根因。
    - ⚠️ `find_first` 对**每个**候选都等满 `timeout_ms`，加候选就是加超时。所以前两个 composer 候选各只探 1s，只有裸兜底吃满 5s。
  - 用户侧症状是 ChatGPT 花 40 秒回"当前这条消息里没有收到可处理的原图文件"。看到这句先查 `upload_stages`，别去查 bridge：bridge 的 `image_count` 和 archive 的 `inbound.images` 那时都是对的。

<!-- nav-check-python: src/browser/chatgpt_page.py:upload_images -->
<!-- nav-check-python: src/browser/chatgpt_page.py:_wait_composer_ready -->
<!-- nav-check-python: src/browser/file_download.py:_recover_from_preview -->
<!-- nav-check-python: src/browser/chatgpt_page.py:attachment_count -->
<!-- nav-check-python: src/browser/human.py:paste_text -->
<!-- nav-check-python: src/utils/errors.py:UPLOAD_FAILED -->
- **多图回复：完成那一帧的图数不可信，必须等回本轮高水位**（08-16）。⚠️ 上面 08-14 那条"生成图 src 会 1→0→1 跳变"仍然成立，但**"stop 熄灭后页面就稳了"这个隐含前提自 2026-08-16 起确认会误导**——实测 stop 熄灭≈重排开始，不是结束。逐帧证据（5 图请求，探针 `auto-3ceb61b8ae325e3ad7565d2e`）：
  - 161.9→169.2s 依次下载 5 个 file_id，DOM 图数 1→3→4→5；**169.5s 起 5 张稳定了 22 秒**（192.0s 仍是 5，stop 全程亮）。
  - 193.5s 页面**重新请求其中 3 个同 id**（不是新图，收尾重渲）；194.5s 完成帧 `stop_present=False`、`action_row_present=True`、**图数塌陷成 3** → 判完成 → 投 3 张。判定时机不早反晚 25 秒，错的是只信这一帧。
  - 修法：`GeneratedImageWatch` 在等待循环里记高水位（单帧最大图数）和 src 并集；判完成后 `_await_stable_generated_images` 把图数等回高水位，上限 `IMAGE_RESCAN_SECONDS`，单图回复第一次扫描即达标、零延迟；等不回来才用"最全的一帧 + 并集"兜底，日志 `generated images never returned to N`。
  - **判定用的采样和投递用的采样必须是同一份**：旧代码 `_capture_image_tokens` 自己又扫了一次 DOM，重渲窗口里两次采样能给出不同的图集。现在由调用方把等稳定后的 src 传进去。
  - 抓下来**按 sha256 去重**：同一张图重渲后换 src，而 media store 每次 put 发随机 token，不去重就会把同一张发两遍。
  - **收发都不限张数**（08-16 产品决定）：出站原 `MAX_IMAGES_PER_REPLY=4`、入站原 `MAX_INPUT_IMAGES=20` 已删除。一条"这几张图分别改"是一图一出，任何固定张数都会静默截掉合法结果。剩下的界是**每张**的 `MAX_IMAGE_BYTES`（20MB）和 media store 的 TTL，不是张数。bridge 侧 `MAX_BRIDGE_IMAGES` 在 AliECS 仓，另行处理。
  - 多图的那些"预览小框"是 48×48 侧栏缩略图，尺寸不到 200px 门槛，靠 `alt` 以"已生成图片/Generated image"开头才被 `generated_image_srcs` 收进来；重渲时 alt 短暂缺失就会掉出计数。实测投递的仍是高清原图（缩略节点的 src 指向原图），不需要按分辨率择优。

<!-- nav-check-python: src/browser/detector.py:GeneratedImageWatch -->
<!-- nav-check-python: src/browser/detector.py:generated_image_srcs -->
<!-- nav-check-python: src/browser/image_input.py:MAX_IMAGE_BYTES -->
- OpenClaw monitor 串行投递图片（慢是设计不是 bug）；⛔ bridge 反转合并别重试。
  - 08-16 实测的后果：同一批 5 张图若被 OpenClaw **逐条**投递（间隔 20-25s，远超 bridge 0.5s 合并窗口），就会变成 **5 轮独立 ChatGPT 往返**，每轮 `upload_stages files=1`、inbound text 是"（已上传文件）"，ChatGPT 逐张回"这张图要怎么改？"。同一天另一次 `files=5` 合并成功，是因为 OpenClaw 那次一次性投了 5 张。**看到"发一批图却触发了 N 轮"先看 `upload_stages files=`，不是 bridge 合并坏了。**
- 文件附件：捕获正则必须容忍 ` (image/*)`；context-summary 历史块要先剥离防死循环。
- **生成文档 pill 点击 = 开预览飞出层，不触发 download**（07-27）。层是 `data-testid=stage-thread-flyout` / `screen-threadFlyOut`，自带 `aria-label=Download`；**Escape 关不掉它**（实测 width 751 扛过多次 Escape），必须点 `data-testid=close-button`。层不关会盖住会话，下一轮永远等不到完成信号 → 整条 lane 被 wedge。每轮发送前会清一次残留层。
- **⛔ 先看这条：能下载的控件只有文件卡片里那个 `button[aria-label='Download file']`**（08-19 实测定案）。ChatGPT 给生成文件的是两个入口，指向同一个文件：
  - 上面那条带虚线下划线的「下载 PDF 文件 / 下载 800×800 PNG 图片」——`class=behavior-btn`，**点了只开预览层，永远不会有 download 事件**。2026-08 整月的丢图丢文件全部来自点它。
  - 文件卡片右侧的下载图标——`button[aria-label='Download file']`（无文本、无 testid，中文界面为「下载文件」）。实测：**4.96s 产生真实 download 事件，`suggested_filename` 就是真实文件名（包装更新_800x800_最新版.pdf，1,049,809 字节），且不会把标签页导航走**。走生产代码全程 11.24s 拿到 `application/pdf`。
  - **⚠️ 这个按钮不能用真实点击，必须在页面内派发 `el.click()`**。实测它 `visible=True`、`opacity=1`、box 36×36，但 **`pointer-events: none`**（卡片被 hover 时才变 auto）——Playwright 的 actionability 里"receives events"这一项永远不成立，所以 `click()` 干等到超时（生产日志就是 `file card download control failed: Locator.click: Timeout 5000ms`），`hover()` 同样超时。`force=True` 也不行：那是真实鼠标事件，会**穿透**到下层元素。派发式点击实测 2.58-8.77s 稳定拿到文件。
  - 因此 `generated_file_targets` 现在**卡片控件优先，有卡片就不再返回 pill**（两个入口指同一文件，混用会把每个文件投递两遍）。定位靠 `control_index`（该按钮在全页同类按钮中的序号）+ `locator.nth`，同一轮里多个文件各自对应各自的卡片。
  - ⚠️ 下面 08-17/08-18/08-19 关于「点 pill → 等预览层 → 抓层里的图」那一整套仍然留着**只作为兜底**（卡片控件不存在时）。**它们记录的排查过程自 2026-08-19 起不再是主路径**——当时没找到这个按钮，才在预览层上绕了一个月。新情况以本条为准。

- **图片走的是另一个预览层，且它没有 Download**（08-17）。⚠️ 上一条只覆盖文档；代码工具产出的图片点开的是 **`data-testid=modal-lightbox-new`**（同时也是 `role=dialog`），实测控件只有 `aria-label=Close` + 两个无 testid 无 aria-label 的按钮 `Save` / `Share`，**没有 `aria-label=Download`，也没有 `<a download>`**。旧代码只认飞出层那两个 testid，于是判成"没有预览层"，白等完整文档预算。
  - 取件顺序：预览层自带下载控件 → 拿不到就抓层里那张图（`backend-api/estuary/content?id=file_...` 原图）→ **无论成败都关层**。关层走 `Close`，实测点一次即消失。
  - ⚠️ 上句原写作「层里渲染的就是 **484×484** 的原图」，**该尺寸自 2026-08-20 起确认会误导**：484×484 只是当时那张方图的渲染尺寸，不是层的固定尺寸。层按等比缩放渲染，**400×800 的竖图在层里是 242×484**。据此写死的尺寸判据见下条。
  - ⛔ **别用尺寸找层里那张图**（08-20 实测定案）。旧判据是"不在 `conversation-turn` 内 + `clientWidth>=300 && clientHeight>=300`"，08-20 00:46 那轮当场翻车：日志写着 `preview layer opened after 0.0s`、candidates 里那张 `inTurn=False` 的图明明在（242×484），却因为宽 242<300 被自己的过滤条件挡掉，`src=None` 直接丢图。**尺寸从一开始就是个错的抽象**——它想表达的是"层里那张"，就该按层容器定位；而且会话里那张也不是缩略图（173×384 的节点，`src` 指向的同样是原图，只是 CSS 缩着显示），拿尺寸区分二者纯属自找麻烦。现在选图复用判层用的同一组 `_PREVIEW_FLYOUT_CONTAINERS`，在层内取渲染面积最大的那张，**层开了但图没找到**这种自相矛盾的状态不会再出现。反证留在 `tests/test_file_download.py`：同一份 DOM 喂旧 JS，竖图返回 `None`、方图返回 src。
  - ⛔ **`Save` 绝对不要点**（08-18 实测）。08-17 记的"Save 打开浏览器自己的保存流程"⚠️ 该说法自 2026-08-18 起确认误导——它听起来像"点了顶多弹个保存框、没有下载事件而已"，实际后果重得多：点 `Save` **不产生任何 download 事件，而是把当前 tab 导航到图片直链** `chatgpt.com/backend-api/estuary/content?id=file_...`，会话页当场没了，该 lane 后续每一轮都在对着一张图片文档作答。恢复要 `go_back` 回会话 URL 再清残留层。取图只走"抓层里那张 img"。
    - 08-20 补充：那条直链**就是层里 `<img>` 的 src**（同 id、同 `ts`/`p`/`sig` 参数）。所以"点 Save 拿地址栏 URL"这条路的信息增益是 0，代价却是丢掉会话页——不要再往这个方向绕。
  - **取字节有两条路，别只留一条**（08-20 加）。默认在页内 `fetch(src, {credentials:'include'})`；全部重试失败后再走 `page.context.request.get(src)`（同 cookie、不碰任何页面）。08-19 11:05 就是 src 完全正确、页内 fetch 报 `!err TypeError` 丢的图。失败日志里两条各自的原因都会印出来（`fetch=… | api:…`）。
  - **点 pill 可能什么都不发生，而且不止一次**（08-18 发现，08-19 加测）。detector 在答案完成那一帧就返回，页面还在收尾重渲，点击落到即将被 React 替换的节点上：既没有 download，层也没开。
    - 08-18 17:37 丢图那轮：`_preview_flyout_visible` 判 False → 直接进抓图兜底 → 对着没开的层空轮询 5s → 关一个不存在的层 3s → `generated file download returned nothing`。事后对同一个 pill 重跑同一段生产代码，9.95s 就拿到 223KB 的 jpg，**pill 和选择器都没问题，只是时机不对**。
    - 08-19 加了"重点一次"之后**又丢了一次**：日志显示重试点击执行了，8s 后仍 `preview image capture failed`，而 `candidates` 里唯一那张图是 **`blob:`** 开头、`inTurn=true`——正是页面把本轮 `blob:` 预览换成 `backend-api/estuary/…` 的那一刻。**两次点击都落在换节点的窗口里，一次重试不够。**
    - 同一 pill 在页面闲下来之后点：**层 0.36s 就开，484×484 的图当场就在**。所以判据不是"等层慢"，是"点得中不中"。现在改成 `_open_preview_layer`：点完轮询等层（`PREVIEW_LAYER_WAIT_SECONDS`），没出现就再点，最多 `PREVIEW_LAYER_CLICK_ATTEMPTS` 次。
    - **层还会在放弃之后才出现**（08-19 用户截图：页面停在全屏预览上，而那轮回复没有图）。所以抓图失败后再等 `PREVIEW_LATE_LAYER_GRACE_SECONDS` 看一眼——晚到的层既救回这张图，也必须被关掉，否则它盖住会话、下一轮永远等不到完成信号。
    - 取证行：`preview layer never opened after N clicks`、`preview layer arrived late`、`preview image capture failed`（带 `src` 和页面所有 img 的尺寸/`inTurn`；**没有候选是 `inTurn=false` = 层压根没开**）。

<!-- nav-check-python: src/browser/file_download.py:FILE_CARD_DOWNLOAD_BUTTON -->
<!-- nav-check-python: src/browser/detector.py:generated_file_targets -->
<!-- nav-check-python: src/browser/file_download.py:PREVIEW_LAYER_WAIT_SECONDS -->
<!-- nav-check-python: src/browser/file_download.py:PREVIEW_LAYER_CLICK_ATTEMPTS -->
<!-- nav-check-python: src/browser/file_download.py:PREVIEW_LATE_LAYER_GRACE_SECONDS -->

  - ⚠️ **已知文档扩展名不做抓图兜底**：PDF 预览同样渲染成 backend 图，抓了会把首页当图片发出去。
  - **pill 的标签可能根本没有扩展名**（实测 `下载 800×800 图片`）。旧代码按后缀判 `is_image`，无后缀 → 当成文档 → 10s 点击 + 50s 等剩余预算 + 5s 点一个不存在的下载控件 = **68 秒白烧且丢图**。现在只有**已知文档后缀**才吃长预算，图片和无后缀都走短探。
  - 抓到的图按魔数嗅探类型并补扩展名，否则无后缀会被 `_guess_content_type` 判成 `application/octet-stream`，投递成文件卡片而不是内联图。
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
| 生成的文件/图片没回到飞书（任何形式） | 先看 api.log 有没有 `file card download control failed`；再看该轮 `reply_stages` 的 `files=` | 08-19 起主路径是文件卡片的 `Download file` 按钮；扫不到卡片才会退回预览层那套。见上「能下载的控件只有…」 |
| ChatGPT 给了「下载 XX 图片」但飞书只收到文字 | `reply_stages` 的 `files=` 是不是几十秒；api.log 找 `preview layer download control unusable` / `preview image capture failed` | 新版 `modal-lightbox-new` 没有 Download 控件，兜底是抓层里的原图。见上「图片走的是另一个预览层」 |
| 同上，但 api.log **只有** `file pill click did not produce a download`，没有后两条 warning，`files≈8s` | 层根本没开：`preview image capture failed` 的 `candidates` 里有没有 `inTurn=false` 的图 | 首次点击落空（08-18），现在会重点一次 pill；仍失败按上一行查 |
| `preview layer opened after …` 有了，`preview image capture failed` 却 `src=None`，candidates 里**有** `inTurn=false` 的图 | 层开了、图也在，是选图规则把它挡了 | 08-20 尺寸门槛坑（竖图 242×484 卡在 300 宽）已改成按层容器定位；再犯说明层的 testid 又变了，核对 `_PREVIEW_FLYOUT_CONTAINERS` |
| `src=` 有完整 URL 但 `bytes=0` | 看 `fetch=` 后面那串：`!err …` 是页内 fetch 挂了，`api:…` 是 `context.request` 也挂了 | 两条都挂才丢图（08-20 起）。只有前半 = 备用路救回来了，不用管 |
| 带图的 `/新对话` 报 UPLOAD_FAILED | `upload_stages` 的 `ready=` 与 `attempts=`；`attached=0` = 三次都没落地 | 本次请求未发送，bridge 会自动改投备机重试一次；连续复现查 composer 结构是否又变了 |
| 页面生成了 N 张图，飞书只收到几张 | archive 数 `outbound.text` 里 `MEDIA:` 行数；api.log 找 `generated images never returned to` | 判定不是判早了：图早就齐了，是完成帧撞上收尾重渲。见上「多图回复」节 |
| 发了图，ChatGPT 却说"没收到图片/请重新上传" | `api.log` 查该轮 `upload_stages`（`attached=0` = 没进输入框）；bridge `image_count` 与 archive `inbound.images` 用来排除上游丢图 | 现在这种情况直接报 `UPLOAD_FAILED` 且不发送，用户重发即可；连续复现查 composer 是否又改了 `ATTACHMENT_PREVIEW` 结构 |
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

## 一次请求到底慢在哪：三条日志连起来看（08-17 起）

跑满硬顶的请求以前在 `send_stages` 和 20 分钟后的超时之间**没有任何一行日志**，既分不清卡在判定还是卡在后处理，也说不出"用户等了 214s 而 ChatGPT 只说 Worked for 10s"差在哪。现在每轮固定产出：

```
upload_stages total=12.57s ready=1.77s/True files=1 attempts=2 input_found=True attached=1 url=...
send_stages   total=2.84s  login=.. flyout=.. input=.. mode=.. snapshot=.. type_delay=.. paste=.. send_btn=.. click=..
wait_signals  t=61s stop=0 stream=0 count=0 text=0 imgs=0 new_img=0 img_loading=0 scaffold=0 status=False actions=1 widget=0 has_new=0 in_progress=0 stable=40 sig_age=61s
reply_stages  total=179.10s images=0 chatgpt=10s wait=110.97 img_settle=0.01 text=0.01 media=0.05 files=68.06
```

- `wait_signals` 每 30s 一行，**纯记录，不参与判定**。`sig_age` 是解释"为什么跑满硬顶"的那一个：idle 兜底要求 progress signature 静止满 `idle_timeout_seconds`，而 signature 里含**全页面**的生成图 src 元组，上一轮的图刷新签名 URL 就会一直把它顶回 0。
- `chatgpt=` 是 ChatGPT 自己写在这一轮上的 `Worked for 10s` / `已思考 12 秒`（`self_reported_work_seconds`，取自 turn 的 innerText，因为 `rich_assistant_text` 会剥掉按钮）。它和 `wait=` 的差额就是**不属于模型的等待**。
- 08-17 实测的一条 214s：`wait=111s`（其中**前 61 秒页面完全空白**，count/text/stop 全 0）+ `files=68s`（无效下载，已修）+ 上传发送 15s，而 `chatgpt=10s`。
- ⚠️ **"提交后页面空白几十秒"目前没有解释**，三次实测分别是 61s、153s、61s。它不是超时（带图软超时 300s），但把它算进任何"该等多久"的判断前，先用 `wait_signals` 取一条真实时间线。

<!-- nav-check-python: src/browser/detector.py:self_reported_work_seconds -->
<!-- nav-check-python: src/browser/detector.py:WAIT_SIGNAL_LOG_INTERVAL_SECONDS -->

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
| job 生命周期 cap | `chat_jobs.py::JOB_LIFECYCLE_GRACE_SECONDS` + 硬顶 | **1230s** | 比浏览器硬顶晚 30s，让浏览器先失败 |

⚠️ **上表这行的旧写法「1200s（07-27 前是代码默认 310s）」自 2026-08-15 起确认会误导**：它读起来像"310 已经是历史"，
实际 07-27 只改了设备上的 `runtime.json`，代码默认一直停在 310——webdock1 因此静默跑了三周 310s。
2026-08-15 已把 `config.py` 与 `lane_scheduler.py` 的默认一并抬到 1200，根因消除；机制和缺键语义见下方
「runtime.json：host 权威，改完必须重启」节。**判断生产行为仍要读设备文件，别只看代码默认**——这次是两者恰好一致了，不是这条规矩失效了。

- **job cap 必须晚于浏览器硬顶**（08-17）。两者都是 1200 时 job 总是先赢——它从提交开始算，浏览器从 detector 开始算，中间差着发送那两三秒。后果是每一次真正跑满硬顶的请求都归档成 `REQUEST_CANCELLED`，detector 自己的超时分支和 `save_debug_dump`（page.html + 截图）**从来没执行过**：08-17 那次 1250s 故障事后一张快照都没有。现在 job cap = 硬顶 + `JOB_LIFECYCLE_GRACE_SECONDS`(30) = 1230s，仍低于 bridge 的 1260s 上限。
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
- ⛔ **切镜像前先在设备上 `docker pull <新 tag>` 确认落地，pull 成功再 render+restart**（2026-08-20 血的教训）。`systemctl restart` 的顺序是**先停旧容器、删掉，再创建新的**；`ExecStartPre=-pull` 前面那个 `-` 意味着拉取失败被忽略，于是流程照走到 create 才报 `No such image`——**旧容器已经没了，新容器起不来，这台机器上没有 webdock**。当天 webdock1 就是这样一度空缺（备机，主力没受影响；换成主力就是生产中断）。先 pull 的话，拉不动只是没变化，容器还在跑。
- ⚠️ **webdock1 拉 ghcr 当前不通**（2026-08-20 实测）：宿主代理 `172.17.0.1:7897` 端口连得上，但到 `ghcr.io:443` 的 TLS 握手被中断——`curl` 报 `SSL_ERROR_SYSCALL`，docker 报 `EOF`。所以 webdock1 暂时切不了新镜像，`secrets/webdock1.enc.env` 的 `WEBDOCK_IMAGE` 留在 `sha-420c488e`，靠热补丁跟上 main。通路修好后切回与 webdock2 同 tag。⚠️ `../AliECS/docs/fleet.md`「拉 GHCR」那张表里没有 webdock1 行，别把 webdock2 的 3.7 MB/s 当成两机通用。
- ⚠️ **容器一重建，热补丁就没了**（同日实证：webdock1 restart 后 `/app/src/browser/file_download.py` 的 md5 从热补版回到镜像版）。所以热补丁只是"等镜像"的临时态，重启后要立刻重打并重启 `python -m src`，验活看 md5。**比对时注意行尾**：仓库里是 LF，从 Windows devbox `cat` 过去的是 CRLF，两者 md5 不同但内容一致——用 `b.replace(b"\r\n", b"\n")` 归一化后再比，别误判成"补丁没生效"。
- `webdock.service` 已加 `ExecStartPre=-pull`（自愈拉镜像）；新机装 `install-ubuntu.sh` 自带。**webdock2 出网可达 ghcr**（2026-08-14 实测，08-20 复测仍可用），换镜像不需要从 devbox 递镜像；要走 bundle 的只有 infra 仓本身，见 `infra/AGENTS.md`「webdock2 同步链路」。
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
