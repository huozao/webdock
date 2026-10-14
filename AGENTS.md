# WebDock Repo Rules

本仓用真实浏览器驱动 ChatGPT 网页。只改任务范围内文件，保护已有未提交改动，默认中文沟通。

## 人工登录红线（本仓完整约束）

1. ChatGPT 登录与 Cloudflare 人机验证必须由人工在 noVNC 完成；完成前 Playwright/CDP 必须 detach，不连接 Chrome。连接 CDP 可能暴露自动化信号，使人工验证循环。
2. 固定顺序：人工登录/过验证 → 会话养熟（cf_clearance）→ 自动化 attach 接管。
3. 未经用户明确同意，不得改变顺序、让自动化在登录/验证阶段连接或驱动浏览器、改为自动登录，也不得重建/重启容器或修改浏览器启动/attach 逻辑而打断已养熟会话。
4. 涉及浏览器、登录态、Cloudflare、attach/detach 的改动先读本节并确认用户授权覆盖具体动作；已有明确授权不重复询问。详细运行机制按 [browser runbook](docs/runbooks/browser.md) 的任务目录读取。

## 修改与授权边界

- 不动 `browser_data/`、`photo_storage/`、`logs/`、`runtime/`；禁止提交 `.env`、真实密钥、浏览器数据或生产数据。
- 未经明确授权，不 commit、push、merge、部署、改远程资源或重启服务。授权后串行检查 status/分支/remote、staged files/diff，显式 add 自己文件。
- 小改动可直推 main，但直推前必须本地 `pytest` 通过；CI 无法阻止已经发生的直推。设备热补丁必须回灌 Git，避免 release 重建丢失。
- 换镜像经 infra 的 SOPS 源更新 `WEBDOCK_IMAGE` 并走已授权的同步/render/服务操作；禁止长期手改渲染 `.env`。
- webdock2 的 SSH 落在 Windows，Linux 命令须 `wsl -d Ubuntu-24.04-WebDock -- <cmd>`；设备路径、直连端口与主备必须现场核验，不能把历史状态当现状。

## 浏览器判据

- 改完成判定前必须有真实请求时间线，覆盖图改图和长任务；新增快通道还需核验纯文本场景。先读 runbook 的关键机制和超时章节，探针步骤见 [开发指南](docs/development.md)。历史 commit message 仅作线索。
- 用 DOM 结构定位元素，不用尺寸/坐标/纵横比代替身份；判层与层内取图共用选择器。会话内图与预览层图可能指向同一原图，不能按渲染尺寸区分。
- JS 选择器用真实 Chromium 最小 DOM 验证；相关 fixture 和反证方法见开发指南。

## 按任务读取

| 任务 | 入口 |
|---|---|
| 本地开发、测试、文档维护 | [docs/development.md](docs/development.md) |
| 浏览器/登录/回复截断/图改图/运行部署 | [browser runbook](docs/runbooks/browser.md) |
| 额度采集/时区/看板/飞书卡片 | [quota-monitor](docs/quota-monitor.md)；只使用采集侧 UTC 归一化的 `*_iso`，下游不得再解析 `Resets …` |
| API/集成/运行操作 | [README](README.md)、[OpenClaw 集成](docs/openclaw-integration.md)、[operations](docs/operations.md) |

设备职责、主备和路径以 AliECS 仓 `docs/fleet.md` 与只读状态为准；工作区可选链接：[fleet](../AliECS/docs/fleet.md)。独立 clone 的本地开发验证不依赖父目录；涉及设备且缺外部事实源时先获取文档，不猜测主机。

## 文档闭环与验证

行为、接口、环境变量、配置或部署步骤变化，同批更新相关现有文档；无文档影响不新建文件。历史可归档但保留入口指向：[指导历史](docs/migrations/2026-09-13-guidance-history.md)。提交带 `Nav-Impact: updated`，或 `Nav-Impact: none` 加 `Nav-Impact-Reason: <依据>`。

```bash
python3 scripts/check_navigation.py
python3 -m unittest discover -s tests -p 'test_*navigation*.py'
```

业务变更按开发指南跑匹配测试；汇报修改文件、验证结果和未验证项。
