# WebDock 开发与文档维护

先读本仓 [AGENTS](../AGENTS.md)，人工登录与 detach 红线不依赖外部文档。浏览器任务再按 [runbook 目录](runbooks/browser.md#按任务读取) 选读。

## 本地验证

在本仓根目录，纯文档修改用 Python 标准库验证，不需要浏览器或相邻仓库：

```bash
python3 scripts/check_navigation.py
python3 -m unittest discover -s tests -p 'test_*navigation*.py'
python3 -m unittest discover -s tests -p test_nav_impact.py
```

业务修改使用项目虚拟环境安装 [requirements.txt](../requirements.txt)，先运行相关测试，再按 CI 的 `pytest -q` 验证；用户授权直推 main 时，本地完整 pytest 通过是推送前置条件。需要真实 Chromium 的测试使用 `rich_markdown_page` fixture；不可把缺浏览器导致的跳过报告为已验证浏览器行为。

<!-- nav-check: scripts/check_navigation.py -->
<!-- nav-check: tests/test_navigation_check.py -->
<!-- nav-check: tests/test_nav_impact.py -->
<!-- nav-check-python: tests/conftest.py:rich_markdown_page -->

## 完成判定与 DOM 取证

修改 `wait_for_response_complete` 前，先按 [浏览器关键机制](runbooks/browser.md#关键机制改代码前必知) 取得真实图改图和长任务时间线；增加完成快通道还要核验纯文本。历史 commit message 和单个旧样本都不能替代本轮证据。

<!-- nav-check-python: src/browser/detector.py:wait_for_response_complete -->

已授权采集时，可临时开启 `runtime.json` 的 `diagnostic_probe_enabled`；指定 `X-Webdock-Probe-ID` 可关联手工请求，未带 header 的真实流量会自动生成 probe id。逐秒 `dom_state` 在 `/app/logs/probes/<probe-id>.jsonl`，采集结束恢复原开关值。生产 runtime、浏览器启动或重启操作仍受 AGENTS 授权边界约束。

选择器验证应喂最小真实 DOM；例如 `test_portrait_preview_image_is_taken_from_inside_the_layer` 验证竖图也能从预览层取出。验证旧缺陷时，对同一 DOM 比较旧判据失败、新判据成功，避免只测新代码自洽。

<!-- nav-check-python: tests/test_file_download.py:test_portrait_preview_image_is_taken_from_inside_the_layer -->

## 运行核验与外部入口

部署流程在 [browser runbook](runbooks/browser.md#部署)，运行参数见 [operations](operations.md)。设备当前事实查 AliECS 仓 `docs/fleet.md`；工作区可选：[fleet](../../AliECS/docs/fleet.md)。主备核验先确认当前 business-cn 主机，再查该机 `/etc/default/webdock-failover-proxy` 与 `127.0.0.1:11800/healthz` 的 `X-Webdock-Device`，不写死服务器。各设备消息存档位于 `/var/log/webdock/archive/<UTC日期>.jsonl`，一行对应一轮对话。

独立 clone 无需这些相邻目录即可开发和验证；需要设备操作却缺事实源时，先获取对应仓文档。源码、设备声明与实时运行状态分开核验，不使用归档里的主备、路径、耗时作为当前值。

## 文档维护契约

行为、API、环境变量、配置、部署或恢复步骤变化，同批更新相关现有 README/runbook；无影响不造文件。过时材料可归档，注明日期和现行替代入口，并从现行文档留指向：[指导历史](migrations/2026-09-13-guidance-history.md)。归档不是当前事实源。

导航覆盖由 [.navigation-check.json](../.navigation-check.json) 登记。正文点名本仓 Python 定义、且不是下划线开头的标识符时，在对应段落旁放符号断言；文档没有提到的符号不因“重要”而额外登记：

```markdown
<!-- nav-check-python: src/main.py:create_app -->
```

校验器收集模块级与类级定义，刻意不进入函数体；函数内局部变量不能作为断言目标。JSON 字段、API 参数或环境变量也不能因与 Python 名字重名而写成符号断言。示例标记必须放代码围栏，行内反引号不会阻止解析。历史材料不新增现行符号/路径断言。

外部链接注明可选，登记精确 `optional_link_prefixes`，不得把本仓断链豁免。提交说明带 `Nav-Impact: updated`，或 `Nav-Impact: none` 与 `Nav-Impact-Reason: <依据>`；CI 的 trailer 检查不能撤销已经发生的直推。
