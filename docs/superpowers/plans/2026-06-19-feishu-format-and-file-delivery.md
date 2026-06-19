# 飞书返回内容：格式支持 + 自动发文件 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务执行。所有步骤用 `- [ ]` 复选框跟踪。**本计划可循环执行**：核心是「每个格式跑一遍夹具测试 → 红→改→绿 → 下一个格式」的闭环，见 §4。

**Goal:** 让飞书这条线路把 ChatGPT 网页回复尽可能完整地还原（标题/数学/任务清单/代码/表格/图片等），并对回复中 ChatGPT 生成的可下载文件，自动下载后直接以飞书文件消息推送给用户（无需点击）。

**Architecture:** 三层链路，三层各自负责一类问题：
1. **提取层** `webdock/src/browser/detector.py::_RICH_MARKDOWN_JS`（DOM→Markdown）——决定"哪些格式被抓到/丢弃/弄乱"。
2. **渲染层** OpenClaw 飞书插件（Markdown→飞书富卡片，在 ECS host 上，`ssh aliecs` 可达，**本次可改**）——决定"飞书端能渲染成什么"。
3. **投递层** `webdock` 出站标记（`MEDIA:`/新增 `FILE:`）+ `AliECS/deploy/openclaw-bridge/openclaw_bridge.py` + OpenClaw 飞书插件——决定"图片/文件怎么发回飞书"。

**Tech Stack:** Python 3.12 / asyncio、patchright(Chromium，webdock 已依赖)、pytest、FastAPI(webdock)、OpenAI-兼容代理(bridge)、飞书开放平台卡片 v2 + 文件消息 API。

---

## 0. 链路事实速查（执行者先读，避免重新摸索）

所有行号基于本仓 `2026-06-19` 现状，执行前用 Grep 复核。

**提取层（webdock，直推 main）**
- `webdock/src/browser/detector.py:163-255` `_RICH_MARKDOWN_JS`：当前**已支持** H1-6 标题、`**粗**`/`*斜*`/`~~删除~~`、行内 `` `code` ``、围栏代码块(带 language)、链接、有序/无序(嵌套)列表、表格、引用、`---`。
- 同文件 `:171` `SKIP = new Set([..., "IMG"])`：**图片被整体丢弃**。`:172 BLOCK`、`:177` WidgetRenderer/not-markdown、`:178` `data-w-component` → 部件/SVG/Mermaid 丢弃（靠截图另发）。
- `detector.py:304 rich_assistant_markdown()`：飞书走这条；空则回退 `rich_assistant_text`(微信纯文本)。
- `webdock/src/browser/chatgpt_page.py:171-177`：`channel == "feishu"` 时用 markdown 而非微信纯文本。
- `chatgpt_page.py:205`：生成图片的出站契约——`result = f"{result}\nMEDIA: {base}/media/{token}"`。图片在页面内 fetch（带登录 cookie）→ 存 `media_store` → `/media/{token}` 暴露。**这是新增"发文件"要复用的模板。**
- `webdock/src/browser/media_store.py:23 put(data, content_type)`：内存存储，`/media/<token>` 服务（`routes_media.py`，免鉴权）。
- `webdock/src/api/routes_media.py`、`webdock/src/main.py:71`：`/media/` 路径豁免鉴权中间件。
- `webdock/src/models/response_models.py:64`：回复以 OpenAI `choices[].message.content` 文本返回（含 `MEDIA:` 标记行）。

**投递/入站层（AliECS bridge，走 PR）**
- `AliECS/deploy/openclaw-bridge/openclaw_bridge.py:30-41`：`FORWARDED_MIME` 已含 image/* 与 `application/pdf`、`.docx`——但这是**入站**(飞书→webdock 上传)。
- 同文件 `:1060` 注释：bridge 会去 webdock `/media` 取图（"/media on WebDock is unauthenticated"）。出站标记到底由 bridge 还是 OpenClaw 解析，**Task 0 recon 拍死**。

**渲染层（OpenClaw 飞书插件，host 上，`ssh aliecs`）**
- 不在本仓。负责把 webdock 返回的 markdown 渲染成飞书富卡片、把 `MEDIA:`/`FILE:` 标记发成飞书图片/文件消息。**Task 0 recon 定位其文件路径与改法。**

**既有测试**
- `webdock/tests/test_rich_markdown.py`：只用 `_FakePage` 喂 canned 值测 Python 包装层，**没有真跑 JS**。循环框架要新建一个用真实 Chromium `set_content` 跑 `_RICH_MARKDOWN_JS` 的夹具测试器（Task 1）。
- `webdock/tests/test_widget_render.py` 已存在，可参考其浏览器夹具写法（若无浏览器夹具则 Task 1 自建）。

---

## 1. 不支持 / 有缺陷格式清单（需求①的答案，Task 0 真机夹具确认后定稿）

| # | 格式 | 现状 | 根因(file:line) | 目标 |
|---|------|------|------|------|
| A | **LaTeX/KaTeX 数学**(`$..$`/`$$..$$`) | 乱码/重复 | `inline()` 把 `.katex-mathml`(MathML)与 `.katex-html`(视觉)两份文本都拼进去 | 读 `annotation[encoding=application/x-tex]` 还原 TeX；飞书端 §A-fallback |
| B | **任务清单复选框**(`- [ ]`/`- [x]`) | 复选框丢失，只剩文字 | `list()` 未识别 `<li>` 内 `input[type=checkbox]` | 还原 `- [ ] `/`- [x] ` |
| C | **上标/下标**(`<sup>`/`<sub>`) | 拍平丢语义(E=mc2) | `inline()` 无 SUP/SUB 分支 | 输出 `^(..)`/`_(..)` 或 Unicode |
| D | **正文内嵌图片**(`<img>` 在 `.markdown` 内/`![](url)`) | 整体丢弃 | `:171 SKIP` 含 `IMG` | markdown 出 `![alt](src)` + 真图走 MEDIA 投递 |
| E | **Mermaid/图表/部件** | 丢弃(设计如此) | `:177-178` WidgetRenderer/SVG | 确认截图确实经 MEDIA 发到飞书(Task 9 验) |
| F | **行内引用角标/citation** | 丢弃或残留 | 渲染为 button/sup-link | 转 `[n]` 链接或显式脚注；至少不残留噪声 |
| G | **表格对齐 / 超宽表** | 对齐丢失(全 `---`)；宽表飞书卡片挤压 | `table()` 固定 `---`；飞书卡片宽度限 | 保留 `:---`/`---:`；超宽→§G-fallback |
| H | **多行围栏代码块在飞书端** | markdown 有，但飞书 lark_md 不渲染多行代码 | 渲染层缺 code 组件映射 | 飞书 code 组件或等宽引用块 fallback |
| I | **深层/混合嵌套列表、有序列表自定义起点** | 部分丢失 | `list()` 只递归 UL/OL 直接子节点 | 夹具覆盖、补齐 |
| J | **分割线 / 多级引用在飞书端** | markdown 有，渲染映射待确认 | 渲染层 | 映射 hr/quote 组件 |
| K | **下载链接→文件**(需求③) | 链接以 `[text](href)` 文本返回，用户得点 | 无出站文件链路 | webdock 下载→`FILE:` 标记→飞书文件消息 |

> 这张表是**活清单**。Task 0 用真机夹具核对每行"现状"，把确认结果写进 `docs/ops/feishu-format-audit-2026-06-19.md`，作为循环的 backlog。

---

## 2. 修复总策略（三层各做什么）

- **提取层(webdock JS)**：把 A–D、F、G、I 在 DOM→Markdown 阶段修对，输出**干净、信息完整的 Markdown**。这是单一事实源，可被夹具测试 100% 自动验证。
- **渲染层(OpenClaw 插件)**：把 Markdown 映射到飞书卡片元素；飞书原生渲染不了的（数学 A、超宽表 G、多行代码 H），按 fallback 规则降级（文本/等宽块/转图片/转文件）。
- **投递层(webdock `FILE:` + bridge + 插件)**：D 的真图、E 的截图、K 的生成文件，统一走"webdock 内存暂存 + `/media` + 标记行"投递。

**飞书端 fallback 规则（渲染层默认值，Task 5 按 recon 调整）**
- 数学(A)：飞书卡片无 LaTeX → 默认输出 ` `$TeX$` ` 行内码文本；增强项=webdock 把公式渲染成 PNG 走 MEDIA。
- 超宽表(G)：列数 > N(默认 6)或总宽超限 → 转成图片(截原表 DOM)或转 CSV 文件(走 FILE)。
- 多行代码(H)：飞书 code 组件优先；不可用则等宽 `text` 元素 + 复制按钮。

---

## 3. 运维约束与红线（必须遵守，来自历史教训）

- **webdock 直推 main 不跑 CI**（ci.yml 只在 PR、release.yml 只 build）→ **每次推 main 前本地 `pytest` 必须全绿**。
- **AliECS 走 PR**（`gh` 已认证）；bridge 改动走 PR。
- **OpenClaw 插件是热改区**：`ssh aliecs` 改完**必须回灌 git**（红线 `hotpatch-must-commit-to-github`），否则 release-deploy rebuild 会用旧码覆盖丢失。
- **登录/过 Cloudflare 人工先做**，automation 不得擅自接管登录流程（红线 `webdock-manual-login-then-automation`）。
- **大量下载/传输验证前先告知用户预估并征得同意**（红线 `notify-before-heavy-download-verification`）。
- Git 写操作串行；推送前核对 `.env`/logs/browser_data 不入库。**未经用户明确要求不提交、不推送。**

---

## 4. 循环验证框架（本计划的引擎）

**自动循环（AI 自主跑，无需真机）**——对清单 §1 的每个格式条目执行：

```
for 格式 in backlog(§1 表):
  1. 取/造夹具      tests/fixtures/feishu/<格式>.html   (ChatGPT 真实 assistant DOM 片段)
  2. 写期望         tests/fixtures/feishu/<格式>.md     (golden markdown)
  3. 跑转换器       pytest -k <格式>  (真实 Chromium set_content → _RICH_MARKDOWN_JS)
  4. 红?  → 改 detector.py 的 JS → 回 3
  5. 绿?  → 跑渲染层契约测试: markdown → 飞书卡片 payload，校验 JSON Schema + fallback
  6. 渲染红? → 改 OpenClaw 插件映射/ fallback → 回 5
  7. 绿 → 该格式标 [x]，下一个
全部绿 → 运行全量 pytest → 输出 docs/ops/feishu-format-audit 更新 → 交真机验收(§Task 9, 用户)
```

**循环的三类自动断言（都不需要真机）**
1. **提取断言**：`_RICH_MARKDOWN_JS(fixture.html) == fixture.md`（Task 1 harness）。
2. **渲染契约断言**：`build_feishu_card(markdown)` 产出的卡片 JSON 通过飞书卡片 Schema 校验，且关键元素存在（如代码块→code 组件、表格→table 组件或 image fallback）（Task 5）。
3. **投递断言**：`FILE:`/`MEDIA:` 标记被正确解析成飞书文件/图片消息体（Task 8 单测，mock 飞书上传）。

**真机验收（用户拍板那一关）**：每轮自动全绿后，AI 生成一条"真机验收单"（§Task 9），列出要在飞书里肉眼确认的项；用户确认后该轮收尾。AI 不自行驱动飞书客户端。

**循环终止条件**：§1 全部条目 `[x]` 且 `pytest` 全绿 且 用户真机验收通过。

---

## 5. 文件结构（创建/修改清单）

**新建**
- `webdock/tests/fixtures/feishu/`：每格式一对 `<name>.html` + `<name>.md`（golden）。
- `webdock/tests/conftest.py`（若无）：加 `rich_markdown_page` 浏览器夹具。
- `webdock/tests/test_rich_markdown_fixtures.py`：夹具驱动的提取断言。
- `docs/ops/feishu-format-audit-2026-06-19.md`：活审计清单 + 每轮结果。
- `docs/ops/openclaw-feishu-plugin-recon.md`：Task 0 recon 产物（插件路径、标记解析归属、卡片能力矩阵）。
- (OpenClaw 仓) 飞书卡片构建器的测试 + fallback 模块（路径由 Task 0 定）。

**修改**
- `webdock/src/browser/detector.py:163-255`：`_RICH_MARKDOWN_JS`（A/B/C/D/F/G/I）。
- `webdock/src/browser/media_store.py`：`put()` 支持 `filename`。
- `webdock/src/api/routes_media.py`：`/media/<token>` 加 `Content-Disposition` 文件名 + 正确 MIME。
- `webdock/src/browser/chatgpt_page.py`：新增下载链接探测/解析 + `FILE:` 标记发射。
- `AliECS/deploy/openclaw-bridge/openclaw_bridge.py`：若 recon 判定 bridge 负责出站，则解析 `FILE:`。
- (OpenClaw 仓) 飞书插件：markdown→卡片映射 + `FILE:`→飞书文件消息。

---

## Task 0: Recon —— 把未知拍死，产出确定事实

**Files:**
- Create: `docs/ops/openclaw-feishu-plugin-recon.md`
- Create: `docs/ops/feishu-format-audit-2026-06-19.md`
- Create: `webdock/tests/fixtures/feishu/`（空目录 + `.gitkeep`）

- [ ] **Step 1: 定位 OpenClaw 飞书插件与出站标记解析归属**

```bash
ssh aliecs 'ls -la /root/.openclaw/plugins 2>/dev/null; grep -rln "MEDIA:\|feishu\|file_key\|im/v1/files\|interactive" /root/.openclaw 2>/dev/null | head'
```
记录到 recon.md：①飞书插件文件绝对路径；②`MEDIA:` 标记当前由 **bridge** 还是 **OpenClaw 插件** 解析（决定 Task 8 改哪边）；③插件现在如何把 markdown 发成飞书消息（纯文本？lark_md？interactive 卡片？）。

- [ ] **Step 2: 飞书卡片能力矩阵**

查飞书开放平台文档，在 recon.md 填表：卡片 v2 是否支持 `markdown`/`table`/`code`/`img`/`hr`/`column_set` 元素、lark_md 支持的语法子集、文件消息 API(`im/v1/files` + `im/v1/messages` msg_type=file)。给出 A(数学)/G(超宽表)/H(代码) 的**最终 fallback 决策**。

- [ ] **Step 3: 采集真机 DOM 夹具**

让 ChatGPT 真机产出覆盖 §1 各格式的回复（数学、任务清单、上下标、内嵌图、代码、宽表、嵌套列表、带 citation、生成可下载 PDF/TXT/Word）。用 webdock 的浏览器会话 dump 最后一条 assistant turn 的 `outerHTML`：

```python
# 一次性脚本：在 webdock 容器/会话内
html = await page.evaluate("""() => {
  const t = document.querySelectorAll("[data-testid^='conversation-turn']");
  return t[t.length-1].outerHTML;
}""")
```
每格式存 `webdock/tests/fixtures/feishu/<name>.html`。**这是循环的燃料**，没有真机夹具就停在这步找用户。

- [ ] **Step 4: 用真机回复核对 §1 清单，定稿审计文档**

把 §1 表抄进 `feishu-format-audit-2026-06-19.md`，逐行标注真机实测现状（确认/修正"现状"列），形成 backlog 勾选表。

- [ ] **Step 5: Commit（不推送）**

```bash
git add docs/ops/openclaw-feishu-plugin-recon.md docs/ops/feishu-format-audit-2026-06-19.md webdock/tests/fixtures/feishu/.gitkeep
git commit -m "docs(feishu): recon plugin + format audit backlog"
```

---

## Task 1: 夹具测试器 —— 用真实 Chromium 跑 `_RICH_MARKDOWN_JS`

**Files:**
- Create/Modify: `webdock/tests/conftest.py`
- Create: `webdock/tests/test_rich_markdown_fixtures.py`
- Test: 自身即测试

- [ ] **Step 1: 写浏览器夹具（conftest）**

```python
# webdock/tests/conftest.py  （若已存在则追加 fixture）
import pathlib
import pytest_asyncio
from patchright.async_api import async_playwright

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "feishu"

@pytest_asyncio.fixture
async def rich_markdown_page():
    """真实 Chromium 页面，供 set_content + evaluate(_RICH_MARKDOWN_JS)。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        yield page
        await browser.close()
```

- [ ] **Step 2: 写夹具驱动断言（先用一个已支持格式自检 harness）**

```python
# webdock/tests/test_rich_markdown_fixtures.py
import pathlib
import pytest
from src.browser.detector import _RICH_MARKDOWN_JS

FIX = pathlib.Path(__file__).parent / "fixtures" / "feishu"

def _cases():
    return sorted(p.stem for p in FIX.glob("*.html"))

@pytest.mark.asyncio
@pytest.mark.parametrize("name", _cases())
async def test_dom_to_markdown(rich_markdown_page, name):
    html = (FIX / f"{name}.html").read_text(encoding="utf-8")
    golden = (FIX / f"{name}.md").read_text(encoding="utf-8").strip()
    await rich_markdown_page.set_content(html)
    out = (await rich_markdown_page.evaluate(_RICH_MARKDOWN_JS)).strip()
    assert out == golden, f"\n--- got ---\n{out}\n--- want ---\n{golden}"
```

- [ ] **Step 3: 放一个 sanity 夹具自检 harness**

用 Task 0 采到的"已支持格式"(如带标题+列表+表格) 存成 `basic.html` + `basic.md`，运行：

Run: `cd webdock && python -m pytest tests/test_rich_markdown_fixtures.py -k basic -v`
Expected: PASS（证明 harness 能真跑 JS 并比对 golden）。若 set_content 后 `.markdown`/`conversation-turn` 选择器抓不到，按夹具实际包裹层补齐外层节点。

- [ ] **Step 4: Commit**

```bash
git add webdock/tests/conftest.py webdock/tests/test_rich_markdown_fixtures.py webdock/tests/fixtures/feishu/basic.*
git commit -m "test(feishu): browser-backed DOM->markdown fixture harness"
```

---

## Task 2: 格式 A —— KaTeX/LaTeX 数学（循环第一例，完整示范）

**Files:**
- Test: `webdock/tests/fixtures/feishu/math.html` + `math.md`
- Modify: `webdock/src/browser/detector.py:163-255`

- [ ] **Step 1: 写失败夹具**

`math.html`（Task 0 真机采，结构形如）:
```html
<div data-testid="conversation-turn-2"><div class="markdown"><p>勾股：
<span class="katex"><span class="katex-mathml"><math><semantics>
<annotation encoding="application/x-tex">a^2+b^2=c^2</annotation></semantics></math></span>
<span class="katex-html" aria-hidden="true">a²+b²=c²</span></span></p>
<div class="katex-display"><span class="katex"><span class="katex-mathml"><math><semantics>
<annotation encoding="application/x-tex">E=mc^2</annotation></semantics></math></span>
<span class="katex-html" aria-hidden="true">E=mc²</span></span></div></div></div>
```
`math.md`（golden）:
```
勾股： $a^2+b^2=c^2$

$$E=mc^2$$
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd webdock && python -m pytest tests/test_rich_markdown_fixtures.py -k math -v`
Expected: FAIL（当前把 mathml+html 两份文本都拼进去，输出含重复/乱码）。

- [ ] **Step 3: 改 `_RICH_MARKDOWN_JS`——inline() 与 block() 识别 KaTeX**

在 `inline()` 循环顶部（`detector.py:184` `for (const c of node.childNodes)` 内，处理 element 之前）加：
```js
      // KaTeX 行内公式：只取原始 TeX，丢弃 mathml/html 两份渲染文本
      if (c.nodeType === 1 && c.classList && c.classList.contains("katex")) {
        const tex = c.querySelector('annotation[encoding="application/x-tex"]');
        if (tex) { out += "$" + (tex.textContent || "").trim() + "$"; continue; }
      }
```
在 `block()` 的 element 分支（`detector.py:232` 附近，`if (/^H[1-6]$/...` 之前）加 display 数学：
```js
      if (c.nodeType === 1 && c.classList && c.classList.contains("katex-display")) {
        const tex = c.querySelector('annotation[encoding="application/x-tex"]');
        if (tex) { out += "$$" + (tex.textContent || "").trim() + "$$\n\n"; continue; }
      }
```
同时把 `katex` 容器从普通递归里排除：`skip()`（`detector.py:173`）末尾返回前加：
```js
    if (n.classList && (n.classList.contains("katex-mathml") || n.classList.contains("katex-html"))) return true;
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd webdock && python -m pytest tests/test_rich_markdown_fixtures.py -k math -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add webdock/src/browser/detector.py webdock/tests/fixtures/feishu/math.*
git commit -m "feat(detector): extract KaTeX as raw TeX for feishu markdown"
```

> 飞书端 A 的 fallback（TeX 文本 vs 公式转图）在 Task 5 处理；本任务只保证提取干净。

---

## Task 3: 格式 B —— 任务清单复选框（完整示范）

**Files:**
- Test: `webdock/tests/fixtures/feishu/tasklist.html` + `tasklist.md`
- Modify: `webdock/src/browser/detector.py` `list()`（`:213-226`）

- [ ] **Step 1: 写失败夹具**

`tasklist.html`:
```html
<div data-testid="conversation-turn-2"><div class="markdown"><ul>
<li class="task-list-item"><input type="checkbox" disabled>未完成项</li>
<li class="task-list-item"><input type="checkbox" checked disabled>已完成项</li>
</ul></div></div>
```
`tasklist.md`:
```
- [ ] 未完成项
- [x] 已完成项
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd webdock && python -m pytest tests/test_rich_markdown_fixtures.py -k tasklist -v`
Expected: FAIL（当前输出 `- 未完成项`，复选框丢失）。

- [ ] **Step 3: 改 `list()` 识别复选框**

把 `detector.py:218` `const marker = ordered ? (i++ + ". ") : "- ";` 替换为：
```js
      const cb = li.querySelector('input[type="checkbox"]');
      let marker = ordered ? (i++ + ". ") : "- ";
      if (cb) marker = "- [" + (cb.checked ? "x" : " ") + "] ";
```
（复选框 input 已被 `inline()` 忽略，不会重复出文本。）

- [ ] **Step 4: 跑测确认通过 + 不回归**

Run: `cd webdock && python -m pytest tests/test_rich_markdown_fixtures.py -v`
Expected: PASS（math、tasklist、basic 全绿）。

- [ ] **Step 5: Commit**

```bash
git add webdock/src/browser/detector.py webdock/tests/fixtures/feishu/tasklist.*
git commit -m "feat(detector): render task-list checkboxes in feishu markdown"
```

---

## Task 4: 格式 C/F/G/I —— 按同一循环模板逐个处理

**对 §1 的 C(上下标)、F(citation)、G(表格对齐/超宽)、I(嵌套列表) 重复 Task 2/3 的五步循环**，每个一对夹具 + 一处 JS 改动：

- [ ] **C 上下标**：`inline()` 加 `if (tag === "SUP") { out += "^(" + inline(c).trim() + ")"; continue; }` 与 `SUB` → `"_(" + ... + ")"`。夹具 `supsub.html/md`。
- [ ] **F citation**：识别 ChatGPT 角标（`a[href]` 带 citation class 或 `sup>a`）→ 输出 `[n](href)`；非链接的部件角标 → 丢弃且不留空 `[]`。夹具 `citation.html/md`。
- [ ] **G 表格对齐**：`table()`（`:204`）读 `th/td` 的 `style="text-align:..."` 或 `align` 属性，分隔行用 `:---`/`:---:`/`---:`。夹具 `table_align.html/md`。**超宽**(列数>6)在 Task 5 渲染层转图/CSV，提取层只保对齐。
- [ ] **I 嵌套列表**：补 `list()` 对 `li` 内**非直接子层**(如 `li>p>ul`)的 UL/OL 递归——把 `:220 for (const ch of li.children)` 改为 `li.querySelectorAll(':scope > ul, :scope > * > ul, :scope > ol, :scope > * > ol')` 或在 `li` 内深度优先找下一层列表。夹具 `nested_list.html/md`。

每条都：写夹具(红)→改 JS→`pytest -k <name>`(绿)→commit。全部完成后跑全量 `pytest` 确认无回归。

---

## Task 5: 渲染层 —— Markdown→飞书富卡片 映射 + fallback（OpenClaw 插件）

**Files:**（路径以 Task 0 recon.md 为准）
- Modify: OpenClaw 飞书插件卡片构建函数
- Create: 插件侧测试（markdown→card payload 契约）
- Reference: `docs/ops/openclaw-feishu-plugin-recon.md`

- [ ] **Step 1: 写契约失败测试**

对每个 markdown 特性断言生成的飞书卡片 payload：
```python
def test_card_code_block_uses_code_element():
    card = build_feishu_card("```python\nprint(1)\n```")
    assert _has_element(card, "code")  # 或 fallback: 等宽 text + 复制
def test_card_table_renders_or_falls_back():
    card = build_feishu_card("| a | b |\n|---|---|\n| 1 | 2 |")
    assert _has_element(card, "table") or _has_image_fallback(card)
def test_card_math_keeps_tex_text():
    card = build_feishu_card("$$E=mc^2$$")
    assert "E=mc^2" in _flatten(card)  # 飞书无 LaTeX，至少保 TeX 文本
```

- [ ] **Step 2: 跑测确认失败**（当前插件多半把 markdown 拍平成纯文本）。

- [ ] **Step 3: 实现映射器**——按 recon 能力矩阵：标题/粗斜体/列表/链接→lark_md；代码块→code 组件或等宽 text；表格→table 组件，列数>6 或宽超限→走 §G fallback(转图/CSV 文件，复用 FILE 投递)；数学→TeX 文本(增强：webdock 渲 PNG 走 MEDIA)；hr/quote→对应组件。**保留 §3 红线：插件改完回灌 git。**

- [ ] **Step 4: 跑测确认通过 + 校验卡片 JSON Schema**（飞书卡片结构合法）。

- [ ] **Step 5: Commit（OpenClaw 仓）+ host 回灌**

```bash
# 本地仓 commit；host 上 ssh aliecs 同步后回灌 git（红线 hotpatch-must-commit-to-github）
```

---

## Task 6: 投递层(webdock) —— `/media` 支持文件名/MIME + `FILE:` 标记

**Files:**
- Modify: `webdock/src/browser/media_store.py`
- Modify: `webdock/src/api/routes_media.py`
- Test: `webdock/tests/test_media_file_serving.py`

- [ ] **Step 1: 写失败测试**

```python
# webdock/tests/test_media_file_serving.py
from src.browser.media_store import MediaStore

def test_put_keeps_filename_and_mime():
    s = MediaStore()
    token = s.put(b"%PDF-1.4 ...", content_type="application/pdf", filename="report.pdf")
    item = s.get(token)
    assert item.content_type == "application/pdf"
    assert item.filename == "report.pdf"
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd webdock && python -m pytest tests/test_media_file_serving.py -v`
Expected: FAIL（`put()` 无 `filename`）。

- [ ] **Step 3: 实现**

`media_store.py::put` 增参 `filename: str | None = None` 并随条目存储；`routes_media.py` 的 `/media/{token}` 响应在有 filename 时加 `Content-Disposition: attachment; filename="..."` 且 `media_type` 用存储的 content_type。

- [ ] **Step 4: 跑测确认通过**

Run: `cd webdock && python -m pytest tests/test_media_file_serving.py -v` → PASS。

- [ ] **Step 5: Commit**

```bash
git add webdock/src/browser/media_store.py webdock/src/api/routes_media.py webdock/tests/test_media_file_serving.py
git commit -m "feat(media): serve files with filename + content-type"
```

---

## Task 7: 投递层(webdock) —— 探测并下载 ChatGPT 生成文件，发 `FILE:` 标记

**Files:**
- Modify: `webdock/src/browser/chatgpt_page.py`（`ask()` 收尾，参考 `:205` MEDIA 模式）
- Create: `webdock/src/browser/file_download.py`
- Test: `webdock/tests/test_file_download.py`
- Reference: `webdock/src/browser/detector.py`（新增探测函数）

> 范围（按你的决策）：**仅 ChatGPT 沙盒生成、带下载链接的文件**（`sandbox:/mnt/data/*.pdf|docx|xlsx|csv|txt|pptx` 与下载按钮卡片）。不处理任意外链。

- [ ] **Step 1: 探测函数 + 失败测试**

```python
# detector.py 新增
DOWNLOAD_SCAN_JS = r"""
() => {
  const t = document.querySelectorAll("[data-testid^='conversation-turn']");
  const el = t[t.length-1]; if (!el) return [];
  const out = [];
  // a) sandbox 链接   b) 文件下载卡片(含 download 按钮/文件名)
  el.querySelectorAll('a[href]').forEach(a => {
    const h = a.getAttribute('href') || '';
    if (h.startsWith('sandbox:') || /\/files\/[^?]+\/download/.test(h) || /mnt\/data/.test(h))
      out.push({href: h, name: (a.textContent||'').trim()});
  });
  return out;
}"""
```
```python
# webdock/tests/test_file_download.py
from src.browser.file_download import parse_download_targets
def test_parse_sandbox_targets():
    raw = [{"href": "sandbox:/mnt/data/report.pdf", "name": "report.pdf"}]
    t = parse_download_targets(raw)
    assert t[0].filename == "report.pdf"
```

- [ ] **Step 2: 跑测确认失败** → `cd webdock && python -m pytest tests/test_file_download.py -v` → FAIL。

- [ ] **Step 3: 实现解析 + 页面内下载**

`file_download.py`：`parse_download_targets()` 归一化(取文件名、推断 MIME)；`download_in_page(page, target)`——`sandbox:` 需先点击对应下载控件/或调 ChatGPT backend files API 拿签名 URL，再 **在页面内 fetch**（带登录 cookie，复用 `:232` 注释的同源 fetch 思路），返回 bytes。**resolve 不到就跳过该文件、保留原文本链接**（不阻断回复）。

- [ ] **Step 4: 在 `ask()` 收尾发 `FILE:` 标记**

文本稳定后：探测→下载→`media_store.put(bytes, mime, filename)`→
```python
result = f"{result}\nFILE: {base}/media/{token} name={filename} mime={mime}".rstrip()
```
（与 `:205` MEDIA 同构。多个文件多行。）

- [ ] **Step 5: 跑测确认通过 + 全量**

Run: `cd webdock && python -m pytest -v`
Expected: PASS（含既有用例无回归）。

- [ ] **Step 6: Commit**

```bash
git add webdock/src/browser/file_download.py webdock/src/browser/detector.py webdock/src/browser/chatgpt_page.py webdock/tests/test_file_download.py
git commit -m "feat(webdock): auto-download ChatGPT-generated files, emit FILE marker"
```

---

## Task 8: 投递层 —— `FILE:` 标记 → 飞书文件消息

**Files:**（改 bridge 还是 OpenClaw 插件，按 Task 0 recon 结论）
- Modify: `AliECS/deploy/openclaw-bridge/openclaw_bridge.py` **或** OpenClaw 飞书插件
- Test: `AliECS/tests/test_openclaw_bridge.py`（或插件侧）

- [ ] **Step 1: 写失败测试**

```python
def test_parse_file_marker():
    text = "见附件\nFILE: http://webdock/media/abc name=report.pdf mime=application/pdf"
    body, files = split_file_markers(text)
    assert body.strip() == "见附件"
    assert files[0] == {"url": "http://webdock/media/abc", "name": "report.pdf", "mime": "application/pdf"}
```

- [ ] **Step 2: 跑测确认失败** → FAIL。

- [ ] **Step 3: 实现**

`split_file_markers()` 正则 `^FILE:\s+(\S+)\s+name=(\S+)\s+mime=(\S+)$`（多行）；把文件从正文剥离。投递侧：取 `/media/<token>` 字节 → 飞书 `im/v1/files`(msg_type=file/stream) 拿 `file_key` → `im/v1/messages` 发 `msg_type=file`。MIME 决定 file_type（pdf/doc/xls/...）。**与既有 MEDIA→飞书图片 同一处理点**（recon 确认）。

- [ ] **Step 4: 跑测确认通过**

Run（bridge）: `cd AliECS && python -m pytest tests/test_openclaw_bridge.py -k file_marker -v` → PASS。

- [ ] **Step 5: Commit + PR（AliECS 走 PR；插件回灌 git）**

```bash
git add -A && git commit -m "feat(bridge): forward FILE markers as feishu file messages"
gh pr create --fill
```

---

## Task 9: 端到端 + 真机验收清单（用户拍板）

**Files:**
- Create: `docs/ops/feishu-verify-2026-06-19.md`

- [ ] **Step 1: 自动全绿门**

Run: `cd webdock && python -m pytest -v` 全绿；bridge/插件契约测试全绿；§1 审计清单全 `[x]`。

- [ ] **Step 2: 部署（按既有流程）**

webdock 直推 main 前**已本地 pytest 全绿**；AliECS PR 合并后 release-deploy；OpenClaw 插件 `ssh aliecs` 同步并**回灌 git**。部署超时留 Created 容器先 `docker ps -a`→`docker start`，别整体重跑。

- [ ] **Step 3: 生成真机验收单，交用户**

让 ChatGPT 真机分别回复：①含 `$$公式$$`②任务清单③代码块④宽表⑤内嵌图⑥嵌套列表⑦生成一个 PDF、一个 TXT、一个 Word（"请把上面内容导出为 pdf/word/txt"）。逐项在飞书肉眼确认：富卡片渲染对、图片到达、**三个文件作为飞书文件消息直接收到且能打开**。AI **不**自行驱动飞书客户端，只列单子等用户回执。

- [ ] **Step 4: 用户回执 → 收尾**

用户确认 OK 则结项；任一项不对 → 回 §4 循环对应格式条目继续。把结果写进 `feishu-verify-2026-06-19.md`。

---

## 自检（Self-Review）

- **需求①(找不支持格式)** → §1 审计表 + Task 0 真机确认。✔
- **需求②(尽量支持)** → 提取层 Task 2/3/4(A–I) + 渲染层 Task 5(飞书卡片映射/fallback)。✔
- **需求③(下载链接→直接发文件)** → Task 6(/media 支持文件) + Task 7(探测下载+FILE 标记) + Task 8(FILE→飞书文件消息) + Task 9(生成 PDF/TXT/Word 真机验)。✔
- **可循环验证** → §4 框架 + 每格式 TDD 五步 + 自动夹具断言(提取/渲染/投递三类) + 真机门。✔
- **类型一致**：`FILE: <url> name=<f> mime=<m>` 标记格式在 Task 7(发) 与 Task 8(解析) 一致；`media_store.put(data, content_type, filename)` 在 Task 6/7 一致。✔
- **运维红线**：webdock 推 main 前本地 pytest、AliECS 走 PR、插件回灌 git、人工登录、下载先告知——已写入 §3 并嵌入相关 Task。✔
- **未决项(交 recon/用户)**：OpenClaw 插件确切路径与出站标记归属(Task 0 Step 1)、飞书卡片能力矩阵与 A/G/H 最终 fallback(Task 0 Step 2)、真机夹具采集(Task 0 Step 3)、真机验收(Task 9)。均显式路由，非占位符。
