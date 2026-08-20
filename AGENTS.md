# WebDock Repo Rules

WebDock 用真实浏览器自动化驱动 ChatGPT 网页，跑在 webdock2（主，Win11+WSL2）/ webdock1（备，Ubuntu 笔记本）。只显式修改任务文件；禁止提交 `.env`、`browser_data`、`photo_storage`、logs、runtime、真实密钥或生产数据。

## ⛔ 红线

ChatGPT 登录与 Cloudflare 验证必须人工在 noVNC 完成，完成前自动化必须 detach。全文见 `../AliECS/AGENTS.md` 首节——涉及浏览器启动、attach/detach、容器重建的改动，先读该节并与用户确认。

## 提交规则

- 小改动可直推 main，**直推前必须本地 `pytest` 通过**（本仓 CI 不拦截，直推=唯一防线）。
- 热补丁在设备上验证成功后，**必须回灌 git 提交**，否则 release 重建会覆盖丢失。
- 用户授权提交/推送后，串行检查 status/分支/remote，显式 add 文件，再直推 main。
- 部署换镜像通过 infra 的 SOPS 源更新 `WEBDOCK_IMAGE`，同步设备并执行 render/restart；禁止长期手改渲染后的 `.env`。

## ⛔ 改 detector 完成判定前，先取一条真实时间线

`wait_for_response_complete` 的每一个信号都有反例，**没有实测就不要动它**。诊断探针跑一次只要几分钟：`runtime.json` 临时置 `diagnostic_probe_enabled=true`，请求带 `X-Webdock-Probe-ID`，逐秒 `dom_state` 落在 `/app/logs/probes/<probe-id>.jsonl`，跑完改回 `false`。至少覆盖图改图和长任务两种，它们的信号次序完全不同。

- 历史 commit message 里的结论只是线索，不是依据。2026-08-14 实测：07-17 记的"stop 按钮在图改图时会 flap"，在当天两条样本里都没出现（渲染 30 秒里 stop 全程亮）。
- 2026-08-12 把协议终态接进判定却没实测图改图，08-14 就回归成"只回 Edit、图丢失"——**新增一条完成快通道时，先问它在图改图/长文档/纯文本三种场景各是什么时序**。
- 各信号的实测层级、反例和阈值写在 `docs/runbooks/browser.md`，改判定前通读那两节。

## ⛔ 找页面元素别用近似量，用结构

**尺寸、坐标、位置、纵横比都是"这东西长什么样"的近似，会在下一种样本形状上静默失效。** 2026-08-20 实例：抓预览层里的图用的判据是"不在 `conversation-turn` 内 + `>=300×300`"，一张 400×800 的竖图在层里渲染成 242×484，宽度差 58px 就被自己的过滤条件丢掉——日志里 `preview layer opened after 0.0s` 和 `src=None` 同时出现，层开着、图在 DOM 里、这一轮却交付了没有图的回复。改成按层容器（`_PREVIEW_FLYOUT_CONTAINERS`）定位后判据不再依赖任何尺寸。

- **同一件事的两处判断必须共用同一组选择器**。"层开了没有"和"层里的图是哪张"用了两套规则，才可能出现"层开了但图找不到"这种自相矛盾的状态；共用一套时，这个状态在结构上就不存在。
- **近似量判据必然带一个静默失效模式**，因为它答的不是你问的问题。要用之前先问：什么形状的样本会让它翻车？答不上来就说明还没找到真正的判据。
- ChatGPT 页面里的"缩略图"不是缩略图：会话内那张 173×384 的节点，`src` 指向的同样是全分辨率原图，只是 CSS 缩着显示。**别用尺寸区分原图和预览图，它们本来就是同一个文件。**

**这类 JS 常量能测，别当成测不了。** `tests/conftest.py` 的 `rich_markdown_page` fixture 是真实 Chromium，`set_content()` 喂一段最小 DOM 就能对 JS 常量断言（见 `test_portrait_preview_image_is_taken_from_inside_the_layer`）。改完顺手把旧判据也对同一份 DOM 跑一遍——旧的返回 `None`、新的返回 src，才算证明这个测试挡得住这个 bug。

<!-- nav-check-python: tests/conftest.py:rich_markdown_page -->

## 排障入口

- 浏览器/登录态/回复截断/图改图 → `docs/runbooks/browser.md`
- 主备判定：先从 `../AliECS/docs/fleet.md` 确认当前 business-cn 主机，再核对该机 `/etc/default/webdock-failover-proxy` 与 `127.0.0.1:11800/healthz` 的 `X-Webdock-Device`；不得固定写死服务器名。
- 消息存档：各机 `/var/log/webdock/archive/<UTC日期>.jsonl`，每对话一行。

## 修改边界

- 不动 `browser_data/`（浏览器登录态）、`photo_storage/`、`logs/`、`runtime/`。
- webdock2 上执行 Linux 命令须 `wsl -d Ubuntu-24.04-WebDock -- <cmd>`；直连 18000 是 502 属正常（走隧道）。

## 文档闭环

代码或配置验证通过后必须回查浏览器 runbook、README、接口、环境变量、部署和恢复步骤。提交带 `Nav-Impact: updated`，或同时带 `Nav-Impact: none` 与 `Nav-Impact-Reason: <依据>`；直推 main 的 CI 只能事后发现缺失 trailer，不代表阻止了该次推送。

### 符号断言判据

治理文档正文中以反引号点名的、指向本仓 Python 定义的、非下划线开头的标识符，必须有对应断言：

```
<!-- nav-check-python: 相对路径.py:符号名 -->
```

且**标记贴在点名它的那段话旁边**，不写进 `.navigation-check.json`——断言与它保护的句子脱钩后，
CI 报红也定位不到该改哪句。注意示例本身必须放在代码围栏里：校验器只剥围栏，行内反引号挡不住
标记正则，写在正文里会被当成真断言（本仓已踩过一次）。

判据只认"文档点了名"，不是"符号重要"：nav-check 的职责是防文档漂移，不是防重构。文档没提的
符号改名不会让文档过期，断言它纯属增加维护成本；反过来，文档点名的符号一改名，那句话立刻就错。

两类**不要**断言：函数内的局部变量（`check_navigation.py` 用 `ast.walk`，局部变量也会命中，
断言等于永远为真）；以及文档里指的其实是 JSON 字段、API 参数或环境变量、只是碰巧与某个无关
文件里的模块级名字重名的标识符。
