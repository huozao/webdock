# 飞书返回格式与文件投递真机验收单

日期：2026-06-19

## 自动门

- WebDock 全量：`python -m pytest -v` -> 135 passed
- AliECS bridge 全量：`python -m pytest tests/test_openclaw_bridge.py -v` -> 40 passed
- WebDock 格式夹具：`python -m pytest tests/test_rich_markdown_fixtures.py -v` -> 10 passed
- WebDock 文件投递单测：
  - `python -m pytest tests/test_media_file_serving.py tests/test_media_store.py -v` -> 6 passed
  - `python -m pytest tests/test_file_download.py tests/test_chatgpt_file_delivery.py -v` -> 3 passed
- AliECS bridge 投递契约：`python -m pytest tests/test_openclaw_bridge.py -k "file_marker or media_proxy_headers" -v` -> 3 passed

## 真机验收项

请在飞书里向当前 ChatGPT 会话分别触发以下回复，并肉眼确认：

1. 数学公式：包含一个行内公式和一个 `$$...$$` 块公式；确认没有 MathML/视觉文本重复乱码，TeX 可读。
2. 任务清单：包含未勾选和已勾选项；确认 `- [ ]` / `- [x]` 语义可见。
3. 多行代码块：包含语言名和多行缩进代码；确认代码块没有被拍平成普通句子。
4. 表格：包含左/中/右对齐列和一个 7 列以上宽表；确认表格内容完整。
5. 内嵌图片或生成图片/部件截图：确认图片能在飞书收到。
6. 嵌套列表、分割线、多级引用：确认层级、分割线、引用语义没有明显丢失。
7. 文件投递：让 ChatGPT 生成并提供一个 PDF、一个 TXT、一个 Word/DOCX；确认三者作为飞书文件消息直接收到，文件名正确且能打开。

## 边界

- 本轮未提交、未推送。
- 本轮未热改 OpenClaw 飞书插件；文件投递复用现有 OpenClaw `MEDIA:` -> `sendMediaFeishu()` 路径。
- 飞书客户端真机渲染与文件打开结果由用户确认。
