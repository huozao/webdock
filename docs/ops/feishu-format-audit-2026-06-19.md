# Feishu Format Audit

Date: 2026-06-19

Scope: ChatGPT web DOM -> WebDock markdown -> OpenClaw Feishu card/file delivery.

## True DOM Fixture Sources

- `webdock/tests/fixtures/feishu/raw/rich_mixed.html`
- `webdock/tests/fixtures/feishu/raw/citation.html`
- `webdock/tests/fixtures/feishu/raw/download_files.html`

## Backlog

| Done | ID | Format | True DOM status | Current issue | Target |
|---|---|---|---|---|---|
| [x] | A | LaTeX/KaTeX math | Confirmed: `katex` with `annotation[encoding="application/x-tex"]` exists. | Fixed by `math` fixture. | Extract raw TeX as `$...$` / `$$...$$`. |
| [x] | B | Task checklist | Confirmed: `ul.contains-task-list`, `li.task-list-item`, `input[type=checkbox]` exist. | Fixed by `tasklist` fixture. | Emit `- [ ]` / `- [x]`. |
| [x] | C | Sup/sub | Partial: true run rendered literal `H<sub>2</sub>O` / `x<sup>2</sup>` text, not DOM `SUP/SUB`. | Fixed by `supsub` regression fixture for real DOM. | Add synthetic/regression fixture for real `SUP/SUB`; preserve literal text safely. |
| [x] | D | Inline image | Confirmed: inline `img alt="OpenAI" src="https://..."` exists. | Fixed by `inline_image` fixture. | Emit `![alt](src)` and keep real image delivery via `MEDIA:` path. |
| [x] | E | Mermaid/widgets | Not in collected fixture. Existing design skips widget/SVG and sends screenshots. | Existing `MEDIA:` screenshot delivery path preserved; final Feishu receipt remains manual Task 9 check. | Keep screenshot-to-`MEDIA:` behavior; verify in Task 9. |
| [x] | F | Citation | Confirmed: `data-testid="webpage-citation-pill"` anchors exist. | Fixed by `citation` fixture. | Emit stable `[source](href)` or numbered source links; no UI noise. |
| [x] | G | Table alignment / wide table | Confirmed: table `th/td` have `style="text-align:..."`; 8-column table exists. | Alignment fixed by `table_align` fixture; wide fallback still delivery-layer work. | Preserve `:---` / `:---:` / `---:`; wide fallback in render/delivery layer. |
| [x] | H | Multiline code block | Confirmed: ChatGPT code block DOM exists. | Covered by `codeblock` fixture. Feishu rich-card rendering remains manual Task 9 check. | Preserve fenced code block markdown. |
| [x] | I | Deep/mixed nested list | Confirmed: nested `ol > li > ul > li > ol` exists. | Fixed by `nested_list` fixture. | Preserve nested list text and numbering. |
| [x] | J | Hr / nested quote | Confirmed: `hr` and nested `blockquote` exist. | Covered by `quote_hr` fixture. Feishu rich-card rendering remains manual Task 9 check. | Preserve divider and nested quote markdown. |
| [x] | K | Download link -> file | Confirmed: generated file buttons exist for TXT/PDF/DOCX, but no `a[href]`. | WebDock now scans generated buttons / sandbox links only, downloads to `/media`, emits `FILE:`; AliECS bridge rewrites `FILE:` to OpenClaw `MEDIA:` and preserves filename header. | Send ChatGPT-generated files as Feishu file messages via existing OpenClaw media path. |

## Task 0 Result

- OpenClaw Feishu plugin path and runtime shape documented in `openclaw-feishu-plugin-recon.md`.
- `MEDIA:` ownership confirmed: OpenClaw core/plugin, bridge only proxies `/media`.
- `FILE:` was unsupported before this run; bridge now rewrites `FILE:` to the existing OpenClaw `MEDIA:` path.
- True DOM fixtures collected without needing manual login intervention.
- Extractor fixtures pass: `python -m pytest tests/test_rich_markdown_fixtures.py -v` -> 10 passed.
- Delivery tests pass:
  - `python -m pytest tests/test_media_file_serving.py tests/test_media_store.py -v` -> 6 passed.
  - `python -m pytest tests/test_file_download.py tests/test_chatgpt_file_delivery.py -v` -> 3 passed.
  - `cd AliECS && python -m pytest tests/test_openclaw_bridge.py -k "file_marker or media_proxy_headers" -v` -> 3 passed.
- Plan `commit` steps are intentionally skipped because this run forbids commit/push without explicit approval.

## Follow-up: Plain Lists Dropped In Feishu

- Symptom: ordinary unordered / ordered lists were visible in ChatGPT web but disappeared in Feishu, including item text.
- Root cause: OpenClaw Feishu `auto` rendering only sends code blocks / tables as markdown cards. List-only replies go through Feishu post `md`, whose list parsing can drop those blocks.
- Fix: `webdock/src/browser/feishu_format.py::feishu_safe_markdown()` converts plain list markers before Feishu delivery:
  - `- item` -> `• item`
  - `1. item` -> `1\. item`
  - `- [x] item` / `- [ ] item` -> `☑ item` / `☐ item`
  - fenced code blocks are left unchanged.
- Verification: `python -m pytest tests/test_feishu_format.py tests/test_rich_markdown_fixtures.py tests/test_chatgpt_file_delivery.py -v` -> 14 passed; `python -m pytest -v` -> 138 passed.
