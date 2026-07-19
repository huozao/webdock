# WebDock Repo Rules

WebDock 用真实浏览器自动化驱动 ChatGPT 网页，跑在 webdock2（主，Win11+WSL2）/ webdock1（备，Ubuntu 笔记本）。只显式修改任务文件；禁止提交 `.env`、`browser_data`、`photo_storage`、logs、runtime、真实密钥或生产数据。

## ⛔ 红线

ChatGPT 登录与 Cloudflare 验证必须人工在 noVNC 完成，完成前自动化必须 detach。全文见 `../AliECS/AGENTS.md` 首节——涉及浏览器启动、attach/detach、容器重建的改动，先读该节并与用户确认。

## 提交规则

- 小改动可直推 main，**直推前必须本地 `pytest` 通过**（本仓 CI 不拦截，直推=唯一防线）。
- 热补丁在设备上验证成功后，**必须回灌 git 提交**，否则 release 重建会覆盖丢失。
- 用户授权提交/推送后，串行检查 status/分支/remote，显式 add 文件，再直推 main。
- 部署换镜像通过 infra 的 SOPS 源更新 `WEBDOCK_IMAGE`，同步设备并执行 render/restart；禁止长期手改渲染后的 `.env`。

## 排障入口

- 浏览器/登录态/回复截断/图改图 → `docs/runbooks/browser.md`
- 主备判定：以 aliecs `/etc/default/webdock-failover-proxy` 为准，经 `127.0.0.1:11800` 探测时看响应头 `X-Webdock-Device`。
- 消息存档：各机 `/var/log/webdock/archive/<UTC日期>.jsonl`，每对话一行。

## 修改边界

- 不动 `browser_data/`（浏览器登录态）、`photo_storage/`、`logs/`、`runtime/`。
- webdock2 上执行 Linux 命令须 `wsl -d Ubuntu-24.04-WebDock -- <cmd>`；直连 18000 是 502 属正常（走隧道）。
