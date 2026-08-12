# WebDock 回复生命周期探针设计

日期：2026-08-12

## 目标

在不创建第二个 CDP 连接、不接触登录/Cloudflare 流程的前提下，复用 WebDock worker 已持有的 Playwright `page`，实测 ChatGPT 回复期间：

1. 动态状态组件和 Stop 按钮的挂载、变化、卸载顺序；
2. ChatGPT 任务相关网络请求是否存在稳定的 `completed`、`error` 或 `aborted` 终态事件；
3. 网络终态、组件卸载、Stop 消失、action row 出现之间的真实时间关系。

本阶段只做取证，不修改 detector 的完成或卡死判定。

## 不在范围内

- 不分析「思考中」「正在搜索」等文字关键词。
- 不通过第二个客户端连接生产 9222。
- 不复制生产 browser profile，不启动新的登录或 Cloudflare 流程。
- 不在本阶段实现 reload 探针、提前判死或新的超时策略。
- 不向真实飞书群发送测试消息。

## 方案选择

### 采用：同一 worker 内的单任务探针

探针运行在现有 WebDock 进程内，使用 BrowserManager 已连接的同一个 `page`。它只为显式指定的探针请求启用，正常请求不创建监听器、不采样 DOM，也不写探针日志。

### 不采用：现有 archive

archive 只能证明最终成功或失败，无法还原瞬时组件生命周期和网络流事件。

### 不采用：第二个 CDP 客户端连接 9222

这会扩大生产浏览器自动化暴露面，并可能触发额外 CDP domain/Cloudflare 风险，违反本次边界。

## 启用边界

探针必须同时满足：

1. 运行时配置 `diagnostic_probe_enabled=true`；
2. 请求携带合法 `X-Webdock-Probe-ID`；
3. Probe ID 只允许 ASCII 字母、数字、点、下划线和连字符，最长 64 字符；
4. 请求使用专用隔离 lane，不复用真实飞书群 lane；
5. 同一时刻最多运行一个探针。

任一条件不满足时，走完全相同的正常请求路径，不产生探针副作用。探针接口继续受现有 Bearer Token 保护。

## 组件划分

### `ResponseLifecycleProbe`

新增独立小模块，职责仅限：

- 在发送前注册页面和网络监听器；
- 接收 detector 每轮的结构化 DOM 状态；
- 将事件写入有界 JSONL；
- 在成功、失败、取消、超时和进程关闭路径中解绑监听器。

它不决定回复是否完成，也不改变 detector 返回值。

### 路由入口

`POST /v1/chat/jobs` 读取 `X-Webdock-Probe-ID` 并在 job runner 的生命周期内创建探针。同步 `/v1/chat/completions` 不启用探针，避免扩大测试面。

### detector 观测出口

detector 每轮已经计算 Stop、streaming、action row、图片和 widget 状态。探针存在时，将这些布尔值和结构签名交给探针；探针不存在时不增加 DOM 查询。

## DOM 观测

每秒记录一次状态变化；状态未变化时不重复写日志。记录：

- `stop_present`
- `streaming_present`
- `action_row_present`
- `assistant_turn_present`
- `generated_image_count`
- `widget_present`
- 最新 assistant turn 的结构签名，不含文本内容
- 最新 assistant turn 内可见动画元素候选的结构信息：tag、role、data-testid、经过裁剪的 class token、CSS animation-name

动态状态组件不靠文字识别。首次实测通过动画元素的挂载/卸载和 DOM 结构找出稳定 selector；在证据不足前不把候选 selector 写入 detector。

禁止记录：`innerText`、输入内容、最终回复正文、HTML 全文。

## 网络观测

监听范围只限探针 page，并按任务开始时间和 ChatGPT conversation 请求路径关联。记录：

- 请求开始、响应头、数据到达、请求完成或失败时间；
- 规范化后的 `origin + path`，删除 query string；
- HTTP 状态、Content-Type、累计数据字节数；
- WebSocket 的 opened/closed 以及脱敏后的事件类型；
- 若 fetch/SSE 完成后可读取响应体，只在内存中解析协议帧，持久化白名单终态字段，例如 `status=completed/error/aborted`，立即丢弃原始 body。

禁止记录：请求/响应 header、Cookie、Token、query 参数、消息正文、工具结果正文、原始 SSE/WebSocket payload。

如果现有 Playwright page 事件无法看见增量 fetch 数据，可在同一 Playwright 连接内为该 page 创建只启用 `Network` domain 的临时 CDP session。不得启用 `Runtime` domain，也不得新连 9222。该 session 同样必须在 `finally` 中关闭。

## 事件格式与资源限制

输出路径：`/app/logs/probes/<probe_id>.jsonl`。

每条事件至少包含：

- 单调时钟偏移毫秒；
- UTC 时间；
- probe ID；
- event type；
- 脱敏后的结构化字段。

单次探针最多 10,000 条或 5 MiB；达到上限后写入一次 `probe_truncated` 并停止采样，但不得影响任务。探针文件在结论整理完成后删除，不进入 Git 或普通 archive。

## 生命周期与错误处理

1. job runner 开始后、发送消息前创建探针；
2. 注册网络监听器并记录初始 DOM 快照；
3. 正常执行现有 ChatGPT ask；
4. 成功、RelayError、取消、hard cap、worker shutdown 均进入 `finally`；
5. `finally` 记录 `probe_end`、解绑所有 page/websocket/CDP 监听器并关闭文件。

探针自身的异常只写 WARNING 并关闭探针，不得改变 job 状态、答案、lane lock 或浏览器页面。

## 实测步骤

### 普通任务

使用隔离 lane，提交一个只返回固定短字符串的任务。验证任务请求、状态组件、Stop、协议终态、组件卸载和 action row 的顺序。

### 长任务

使用另一个隔离 lane，提交会产生明显处理阶段但不向外部系统写数据的长任务。至少运行到状态组件出现并发生一次结构变化，等待正常完成。

### 可选中止任务

如果前两条无法观察 `aborted`，再使用第三个隔离 lane，通过 job DELETE 取消任务，验证 aborted/cancelled 的网络和 DOM 表现。该步骤只在前两条完成后执行。

## 成功标准

实测完成后必须能回答：

1. 是否存在稳定、可关联到本次请求的网络终态事件；
2. 终态事件是 `completed/error/aborted` 还是仅表现为流关闭；
3. 状态组件是否有不依赖文字的稳定结构 selector；
4. 网络终态、Stop 消失、组件卸载、action row 出现的先后和时间差；
5. reload 方案以后应以哪一个信号为主、哪些信号只作校验。

如果没有可重复的网络终态事件，结论必须明确写成“协议终态不存在或当前观测方式不可见”，不得把网络静默解释为完成。

## 验证

- 单元测试覆盖：未启用零副作用、Probe ID 校验、事件脱敏、大小上限、所有终态解绑、探针异常不影响正常 job。
- WebDock 全量 `pytest -q`。
- 部署后先确认两个节点健康和生产路由，再只在 primary 的隔离 lane 启用探针。
- 实测完成后确认监听器数量回到基线，其他 lane 无探针文件、无状态变化。

## 发布与回退

探针代码默认关闭，按 WebDock 正常镜像和 infra pin 流程发布。若探针影响正常请求，立即关闭运行时开关即可停止采样；无需变更 detector 算法。是否保留默认关闭的诊断能力，在本轮实测结论和后续计划确认时再决定。
