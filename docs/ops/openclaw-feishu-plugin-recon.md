# OpenClaw Feishu Plugin Recon

Date: 2026-06-19

## Runtime Findings

- ECS control path: `ssh aliecs`.
- Running containers:
  - `openclaw-openclaw-gateway-1`: `ghcr.io/openclaw/openclaw:2026.6.5`
  - `openclaw-bridge`: `ghcr.io/huozao/openclaw-bridge:V20260619176`
- Installed Feishu plugin package:
  - `/root/.openclaw/npm/projects/openclaw-feishu-dc69f44688/node_modules/@openclaw/feishu`
  - package version: `@openclaw/feishu@2026.6.5`
  - upstream package repo: `https://github.com/openclaw/openclaw`

## Outbound Marker Ownership

- `MEDIA:` is parsed by OpenClaw core, not by `openclaw-bridge`.
  - Core parser: `/app/dist/payloads-DT8nnhH8.js`, `MEDIA_TOKEN_RE`.
  - Core payload resolver: `/app/dist/reply-payload-D-VMfpYI.js`, `resolvePayloadMediaUrls()`.
  - Feishu runtime consumes `mediaUrls` and calls `sendMediaFeishu()`.
- `openclaw-bridge` only proxies `/media/<token>` to WebDock so OpenClaw can fetch WebDock media.
- `FILE:` has no native OpenClaw parser. This run implements the lowest-risk path in `openclaw-bridge`: rewrite `FILE:` to existing `MEDIA:` markers and preserve `/media` `Content-Disposition`, so OpenClaw core/plugin reuse the existing outbound media path.

## Current Feishu Sending Shape

- Plain text path: `sendMessageFeishu()`.
- Markdown/card path: `sendMarkdownCardFeishu()` -> `buildMarkdownCard()` -> Feishu `interactive` card.
- Feishu runtime uses card mode when `renderMode` is `card`, or `auto` plus card-worthy text.
- Native sanitized cards currently accept a small subset: `markdown`, `hr`, and `button`; richer table/code handling should either use Feishu card JSON 2.0 components or keep markdown fallback.
- The plugin already has file upload/send primitives:
  - `uploadFileFeishu()` uses Feishu `im/v1/files`.
  - `sendFileFeishu()` sends `msg_type=file`/related file message types with `file_key`.
  - `sendMediaFeishu()` downloads URL media, resolves image vs file from content type / file name, and routes non-images through `uploadFileFeishu()` + `sendFileFeishu()`.

## Feishu Capability Matrix

Sources checked: official Feishu Open Platform docs for card rich text, table, image, column layout, send message content structure, and send message API.

| Capability | Official support | Decision |
|---|---:|---|
| Rich text / Markdown | Yes, card JSON 2.0 rich text supports headings, images, tables, code blocks, dividers, etc. | Use card rich text markdown as default. |
| Table component | Yes, card table component supports plain text and markdown cell data types. | Preserve markdown table extraction; render as card table when plugin mapping is added. |
| Code block | Yes in JSON 2.0 rich text markdown. | Prefer rich text code block; fallback to fenced code text. |
| Image | Yes, card image module and image messages exist. | Existing `MEDIA:` image path remains valid. |
| Divider / hr | Yes. | Map `---` to card divider/hr where possible. |
| Column set | Yes, legacy column layout supports text, Markdown, image, divider. | Optional layout tool, not required for first pass. |
| File message | Yes, upload through `im/v1/files`, send message with file content. | WebDock emits `FILE:`; bridge rewrites to `MEDIA:`; Feishu plugin `sendMediaFeishu()` sends as file based on content type / file name. |

## Fallback Decisions

- A math: Feishu has no native LaTeX rendering guarantee. Preserve TeX as inline/block code text in cards. Formula-to-image can be a later enhancement.
- G wide table: keep extracted markdown and alignment. If columns > 6, prefer CSV/Markdown file fallback via `FILE:` or screenshot image via `MEDIA:` rather than cramped card table.
- H code block: use Feishu card rich text Markdown code block first; if runtime/card rejects it, send fenced code as plain markdown text.

## True DOM Fixtures Collected

Raw fixtures are stored under `webdock/tests/fixtures/feishu/raw/`:

- `rich_mixed.html`: real ChatGPT assistant DOM containing KaTeX display math, task-list checkboxes, literal sub/sup input text, inline image, code block, aligned table, 8-column table, nested list, hr, and nested quote.
- `citation.html`: real ChatGPT assistant DOM containing `data-testid="webpage-citation-pill"` links to OpenAI docs.
- `download_files.html`: real ChatGPT assistant DOM containing generated-file buttons (`behavior-btn entity-underline`) for `feishu_test.txt`, `feishu_test.pdf`, and `feishu_test.docx`; no normal `a[href]` was present.

## Remaining Manual Checks

- No OpenClaw plugin hotpatch was performed, so no plugin git backfill is required for this run.
- Task 9 must manually confirm in Feishu that PDF/TXT/Word arrive as file messages and open correctly.
