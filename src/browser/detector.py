from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from src.browser import selectors
from src.browser.file_download import DownloadTarget, parse_download_targets
from src.browser.human import idle_mouse_movement

# Grace fallback for a residual .result-streaming class that lingers with NO stop
# button: if the reply text has been stable this many seconds beyond stable_seconds
# we return anyway, so a stuck streaming indicator can't hang until timeout. The
# stop button is treated as the authoritative "still generating" signal and is NOT
# bypassed by this grace — while it's present we keep waiting.
STUCK_GRACE_SECONDS = 8

# While ChatGPT generates an image it shows a loading placeholder whose
# data-testid starts with 'image-gen-loading-state'; it disappears once the <img>
# renders. This is the reliable "an image reply is in progress" signal — unlike
# text keywords it is ABSENT when ChatGPT refuses or replies with text, so a
# refusal/text reply returns normally instead of hanging on an image that never comes.
_IMAGE_GENERATING_JS = """
() => !!document.querySelector("[data-testid^='image-gen-loading-state']")
"""
# ChatGPT shows interim status text while it's still working — thinking, searching
# the web, reading/browsing pages, analyzing files/data, running code, generating
# an image, editing canvas/docs, navigating, etc. While any of these show, the
# reply isn't finished, so keep waiting until they disappear. A finished reply or a
# refusal matches none of these. Covers the ZH + EN variants ChatGPT uses.
_INTERIM_RE = re.compile(
    r"正在(思考|搜索|浏览|读取|分析|运行|生成|创作|绘制|画|更新|编辑|执行|导航|完成|处理|查找|制作|检索)|"
    r"生成更细致|请稍候|搜索网页|读取网页|读取附件|运行代码|更新画布|编辑文档|完成任务|"
    r"\b(thinking|searching|browsing|reading|analy[sz]ing|generating|creating|drawing|rendering|"
    r"editing|updating|running|navigating|working)\b|"
    r"^(let me|i'?ll|i am going to|i will|the user (asks|wants|is asking|requested))",
    re.IGNORECASE,
)
# A real ChatGPT-generated image (DALL-E etc) renders as <img> with a backend
# estuary/content (or oaiusercontent) src at a real size.
_GENERATED_IMG_SRCS_JS = """
(minPx) => {
  const out = [];
  const seen = new Set();
  for (const im of document.querySelectorAll('img')) {
    const turn = im.closest("[data-testid^='conversation-turn']");
    if (turn && turn.querySelector("[data-message-author-role='user']")) continue;
    const src = im.currentSrc || im.src || '';
    if (!src || seen.has(src)) continue;
    if (!/backend-api\\/(estuary|files)\\/|oaiusercontent/.test(src)) continue;
    // Two acceptance rules — multi-image replies render extra candidates as 48x48
    // side-rail thumbnails (user must click one to swap it into the main view);
    // a size-only filter delivers just the currently-selected main image to the
    // chat. Recognize the thumbnails via alt="已生成图片"/"Generated image".
    const largeEnough = im.clientWidth >= minPx && im.clientHeight >= minPx;
    const generatedAlt = im.alt === '已生成图片' || im.alt === 'Generated image';
    if (!largeEnough && !generatedAlt) continue;
    seen.add(src);
    out.push(src);
  }
  return out;
}
"""
_DOWNLOAD_SCAN_JS = """
() => {
  const turns = document.querySelectorAll("[data-testid^='conversation-turn']");
  const el = turns.length ? turns[turns.length - 1] : document;
  if (!el) return [];
  if (el.querySelector && el.querySelector("[data-message-author-role='user']")) return [];
  const out = [];
  for (const a of el.querySelectorAll("a[href]")) {
    out.push({
      kind: "link",
      href: a.getAttribute("href") || a.href || "",
      text: (a.innerText || a.textContent || "").trim(),
      download: a.getAttribute("download") || ""
    });
  }
  for (const b of el.querySelectorAll("button.behavior-btn, button.entity-underline")) {
    out.push({
      kind: "button",
      href: "",
      text: (b.innerText || b.textContent || "").trim(),
      download: ""
    });
  }
  return out;
}
"""


async def generated_image_srcs(page: Any, min_px: int = 200) -> list[str]:
    """Srcs of ChatGPT-generated images (DALL-E etc; backend estuary/content)
    rendered at a real size, de-duped. Empty list on failure."""
    try:
        srcs = await page.evaluate(_GENERATED_IMG_SRCS_JS, min_px)
        return list(srcs) if srcs else []
    except Exception:
        return []


async def generated_file_targets(page: Any) -> list[DownloadTarget]:
    """ChatGPT-generated file download targets in the latest assistant reply.
    Filtering lives in file_download.py so arbitrary external links are ignored."""
    try:
        raw = await page.evaluate(_DOWNLOAD_SCAN_JS)
    except Exception:
        raw = []
    return parse_download_targets(raw)


async def has_generated_image(page: Any, min_px: int = 200) -> bool:
    """True if any ChatGPT-generated image is rendered at a real size."""
    return bool(await generated_image_srcs(page, min_px))


async def image_generating(page: Any) -> bool:
    """True while ChatGPT is generating an image (loading-state placeholder shown).
    Distinguishes an in-progress image reply from a refusal / plain-text reply."""
    try:
        return await page.evaluate(_IMAGE_GENERATING_JS)
    except Exception:
        return False


# Walk the latest assistant message DOM into WeChat-friendly plain text:
# - tables -> space-aligned text (CJK width aware)
# - lists  -> "• item"
# - drops buttons/svg/role=button (copy / read-aloud UI) so they don't pollute
#   the text or the stability check.
# Pure read (no DOM mutation). Falls back to inner_text if this returns empty.
_RICH_TEXT_JS = r"""
() => {
  // ChatGPT virtualizes turns and, for image/reasoning replies, no longer marks
  // the assistant message with data-message-author-role nor wraps prose in
  // .markdown. Anchor on the LAST conversation-turn instead: if it's the user's
  // own message the assistant hasn't replied yet (-> ""); otherwise its prose (if
  // any) lives in .markdown, while the reasoning toggle ("已思考"/"Thought for…")
  // and action buttons sit OUTSIDE it. A pure image/widget reply has NO .markdown
  // -> "" (the image/widget is delivered separately). Anchoring on the latest turn
  // means we never return stale text from an earlier turn.
  //
  // A "preamble -> 已思考/Thought -> answer" reply puts the opening line and the
  // real answer in SEPARATE .markdown blocks inside the same turn, so we must walk
  // EVERY .markdown (not just the first) or we drop the answer and return only the
  // preamble.
  const turns = document.querySelectorAll("[data-testid^='conversation-turn']");
  const el = turns[turns.length - 1];
  if (!el) return "";
  if (el.querySelector("[data-message-author-role='user']")) return "";
  const roots = el.querySelectorAll(".markdown");
  if (!roots.length) return "";
  const dw = (s) => { let w = 0; for (const ch of s) { w += (ch.codePointAt(0) > 255 ? 2 : 1); } return w; };
  const pad = (s, n) => s + " ".repeat(Math.max(0, n - dw(s)));
  const SKIP = new Set(["BUTTON", "SVG", "PATH", "USE", "SCRIPT", "STYLE"]);
  const BLOCK = new Set(["P", "DIV", "H1", "H2", "H3", "H4", "H5", "H6", "PRE", "BLOCKQUOTE", "SECTION", "ARTICLE", "UL", "OL"]);
  const tableToText = (t) => {
    const rows = [...t.querySelectorAll("tr")].map((tr) =>
      [...tr.querySelectorAll("th,td")].map((c) => (c.innerText || c.textContent || "").replace(/\s+/g, " ").trim())
    ).filter((r) => r.length);
    if (!rows.length) return "";
    const cols = Math.max(...rows.map((r) => r.length));
    const w = new Array(cols).fill(0);
    rows.forEach((r) => r.forEach((c, i) => { w[i] = Math.max(w[i], dw(c)); }));
    const fmt = (r) => r.map((c, i) => pad(c || "", w[i])).join("  ").replace(/\s+$/, "");
    const lines = [fmt(rows[0]), w.map((x) => "-".repeat(x)).join("  ")];
    rows.slice(1).forEach((r) => lines.push(fmt(r)));
    return lines.join("\n");
  };
  const walk = (node) => {
    let out = "";
    for (const child of node.childNodes) {
      if (child.nodeType === 3) { out += child.nodeValue; continue; }
      if (child.nodeType !== 1) continue;
      const tag = child.tagName;
      if (SKIP.has(tag)) continue;
      if (child.getAttribute && child.getAttribute("role") === "button") continue;
      // Skip ChatGPT widget containers (clock/weather/etc) - they are captured
      // as screenshots separately; their text is noise (e.g. clock dial digits).
      const cls = typeof child.className === "string" ? child.className : "";
      if (cls.indexOf("WidgetRenderer") >= 0 || cls.indexOf("not-markdown") >= 0) continue;
      if (child.getAttribute && child.getAttribute("data-w-component")) continue;
      if (tag === "TABLE") { out += "\n" + tableToText(child) + "\n"; continue; }
      if (tag === "BR") { out += "\n"; continue; }
      if (tag === "LI") { out += "\n• " + walk(child).trim(); continue; }
      const inner = walk(child);
      if (BLOCK.has(tag)) { out += "\n" + inner + "\n"; }
      else { out += inner; }
    }
    return out;
  };
  const parts = [];
  for (const root of roots) parts.push(walk(root));
  return parts.join("\n\n").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}
"""


# Walk the latest assistant message DOM into Markdown, preserving structure that
# rich channels (Feishu) render: headings, bold/italic, inline code, fenced code
# blocks (with language), links, ordered/unordered (nested) lists, tables, block
# quotes and rules. Block-level elements are skipped inside inline() so a list/
# table/code block never leaks into a paragraph. Widgets/buttons/SVG are dropped
# (delivered as screenshots separately). Pure read; falls back to plain rich text.
_RICH_MARKDOWN_JS = r"""
() => {
  const turns = document.querySelectorAll("[data-testid^='conversation-turn']");
  const el = turns[turns.length - 1];
  if (!el) return "";
  if (el.querySelector("[data-message-author-role='user']")) return "";
  const roots = el.querySelectorAll(".markdown");
  if (!roots.length) return "";
  const SKIP = new Set(["BUTTON", "SVG", "PATH", "USE", "SCRIPT", "STYLE"]);
  const BLOCK = new Set(["P","H1","H2","H3","H4","H5","H6","UL","OL","PRE","BLOCKQUOTE","TABLE","HR","DIV","SECTION","ARTICLE"]);
  const skip = (n) => {
    if (SKIP.has(n.tagName)) return true;
    if (n.getAttribute && n.getAttribute("role") === "button") return true;
    const cls = typeof n.className === "string" ? n.className : "";
    if (cls.indexOf("WidgetRenderer") >= 0 || cls.indexOf("not-markdown") >= 0) return true;
    if (n.getAttribute && n.getAttribute("data-w-component")) return true;
    if (n.classList && (n.classList.contains("katex-mathml") || n.classList.contains("katex-html"))) return true;
    return false;
  };
  const inline = (node) => {
    let out = "";
    for (const c of node.childNodes) {
      if (c.nodeType === 3) { out += c.nodeValue; continue; }
      if (c.nodeType === 1 && c.classList && c.classList.contains("katex")) {
        const tex = c.querySelector('annotation[encoding="application/x-tex"]');
        if (tex) { out += "$" + (tex.textContent || "").trim() + "$"; continue; }
      }
      if (c.nodeType !== 1 || skip(c)) continue;
      const tag = c.tagName;
      if (BLOCK.has(tag)) continue;
      if (tag === "BR") { out += "  \n"; continue; }
      if (tag === "STRONG" || tag === "B") { out += "**" + inline(c).trim() + "**"; continue; }
      if (tag === "EM" || tag === "I") { out += "*" + inline(c).trim() + "*"; continue; }
      if (tag === "DEL" || tag === "S") { out += "~~" + inline(c).trim() + "~~"; continue; }
      if (tag === "SUP") { out += "^(" + inline(c).trim() + ")"; continue; }
      if (tag === "SUB") { out += "_(" + inline(c).trim() + ")"; continue; }
      if (tag === "CODE") { out += "`" + (c.innerText || c.textContent || "") + "`"; continue; }
      if (tag === "IMG") {
        const src = c.getAttribute("src") || "";
        const alt = (c.getAttribute("alt") || "").replace(/\]/g, "\\]").replace(/\n/g, " ").trim();
        if (src) out += "![" + alt + "](" + src + ")";
        continue;
      }
      if (tag === "A") {
        const href = c.getAttribute("href") || "";
        if (c.closest && c.closest('[data-testid="webpage-citation-pill"]')) {
          const txt = (c.innerText || c.textContent || "").replace(/\s+/g, " ").trim() || "source";
          out += href ? "[" + txt + "](" + href + ")" : txt;
          continue;
        }
        const txt = inline(c).trim() || href;
        out += href ? "[" + txt + "](" + href + ")" : txt;
        continue;
      }
      out += inline(c);
    }
    return out;
  };
  const cell = (c) => inline(c).replace(/\s+/g, " ").trim().replace(/\|/g, "\\|");
  const table = (t) => {
    const rowCells = [...t.querySelectorAll("tr")].map((tr) => [...tr.querySelectorAll("th,td")]).filter((r) => r.length);
    const rows = rowCells.map((r) => r.map(cell));
    if (!rows.length) return "";
    const cols = Math.max(...rows.map((r) => r.length));
    const fill = (r) => { const a = r.slice(); while (a.length < cols) a.push(""); return a; };
    const alignFor = (c) => {
      const raw = ((c && c.getAttribute && c.getAttribute("align")) || (c && c.style && c.style.textAlign) || "").toLowerCase();
      if (raw === "center") return ":---:";
      if (raw === "right") return "---:";
      return raw === "left" ? ":---" : "---";
    };
    const headerCells = rowCells[0].slice();
    while (headerCells.length < cols) headerCells.push(null);
    const lines = ["| " + fill(rows[0]).join(" | ") + " |", "| " + headerCells.map(alignFor).join(" | ") + " |"];
    rows.slice(1).forEach((r) => lines.push("| " + fill(r).join(" | ") + " |"));
    return lines.join("\n");
  };
  const list = (node, ordered, depth) => {
    let out = "";
    let i = ordered ? (parseInt(node.getAttribute("start") || "1", 10) || 1) : 1;
    for (const li of node.children) {
      if (li.tagName !== "LI") continue;
      const cb = li.querySelector('input[type="checkbox"]');
      let marker = ordered ? (i++ + ". ") : "- ";
      if (cb) marker = "- [" + (cb.checked ? "x" : " ") + "] ";
      let text = inline(li).trim();
      if (!text) {
        const chunks = [];
        for (const ch of li.children) {
          if (ch.tagName === "UL" || ch.tagName === "OL") continue;
          const t = inline(ch).trim();
          if (t) chunks.push(t);
        }
        text = chunks.join(" ");
      }
      out += "  ".repeat(depth) + marker + text + "\n";
      const nested = [];
      for (const ch of li.children) {
        if (ch.tagName === "UL" || ch.tagName === "OL") nested.push(ch);
        else nested.push(...ch.querySelectorAll(":scope > ul, :scope > ol"));
      }
      for (const ch of nested) {
        if (ch.tagName === "UL") out += list(ch, false, depth + 1);
        else if (ch.tagName === "OL") out += list(ch, true, depth + 1);
      }
    }
    return out;
  };
  const block = (node) => {
    let out = "";
    for (const c of node.childNodes) {
      if (c.nodeType === 3) { if (c.nodeValue && c.nodeValue.trim()) out += c.nodeValue.trim() + " "; continue; }
      if (c.nodeType !== 1 || skip(c)) continue;
      const tag = c.tagName;
      if (c.classList && c.classList.contains("katex-display")) {
        const tex = c.querySelector('annotation[encoding="application/x-tex"]');
        if (tex) { out += "$$" + (tex.textContent || "").trim() + "$$\n\n"; continue; }
      }
      if (/^H[1-6]$/.test(tag)) { out += "\n" + "#".repeat(+tag[1]) + " " + inline(c).trim() + "\n\n"; continue; }
      if (tag === "P") { const t = inline(c).trim(); if (t) out += t + "\n\n"; continue; }
      if (tag === "UL") { out += list(c, false, 0) + "\n"; continue; }
      if (tag === "OL") { out += list(c, true, 0) + "\n"; continue; }
      if (tag === "PRE") {
        const code = c.querySelector("code");
        const cls = code && typeof code.className === "string" ? code.className : "";
        const m = cls.match(/language-([\w+#.-]+)/);
        const txt = ((code || c).innerText || (code || c).textContent || "").replace(/\n+$/, "");
        out += "```" + (m ? m[1] : "") + "\n" + txt + "\n```\n\n"; continue;
      }
      if (tag === "BLOCKQUOTE") { out += block(c).trim().split("\n").map((l) => "> " + l).join("\n") + "\n\n"; continue; }
      if (tag === "TABLE") { out += table(c) + "\n\n"; continue; }
      if (tag === "HR") { out += "---\n\n"; continue; }
      out += block(c);
    }
    return out;
  };
  const parts = [];
  for (const root of roots) parts.push(block(root));
  return parts.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}
"""


async def find_first(page: Any, selector_list: list[str], *, visible: bool = False, timeout_ms: int = 1000) -> str | None:
    state = "visible" if visible else "attached"
    for selector in selector_list:
        try:
            await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
            return selector
        except Exception:
            continue
    return None


async def any_selector_found(page: Any, selector_list: list[str]) -> bool:
    for selector in selector_list:
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


async def latest_assistant_text(page: Any) -> str:
    for selector in selectors.ASSISTANT_MESSAGE:
        try:
            items = page.locator(selector)
            count = await items.count()
            if count > 0:
                text = await items.nth(count - 1).inner_text(timeout=1500)
                return _clean_assistant_text(text)
        except Exception:
            continue
    return ""


async def rich_assistant_text(page: Any) -> str:
    """WeChat-friendly text of the latest assistant message (tables aligned, UI
    elements dropped). Falls back to inner_text if the DOM walk yields nothing."""
    try:
        text = await page.evaluate(_RICH_TEXT_JS)
    except Exception:
        text = ""
    if not text:
        return await latest_assistant_text(page)
    return _clean_assistant_text(text)


async def rich_assistant_markdown(page: Any) -> str:
    """Markdown of the latest assistant message (tables/code/links/emphasis kept),
    for channels that render markdown (Feishu). Falls back to the plain WeChat-style
    text if the markdown walk yields nothing."""
    try:
        text = await page.evaluate(_RICH_MARKDOWN_JS)
    except Exception:
        text = ""
    if not text or not text.strip():
        return await rich_assistant_text(page)
    return text.strip()


async def assistant_message_count(page: Any) -> int:
    for selector in selectors.ASSISTANT_MESSAGE:
        try:
            count = await page.locator(selector).count()
            if count > 0:
                return count
        except Exception:
            continue
    return 0


async def is_generating(page: Any) -> bool:
    """True while ChatGPT is still streaming a reply (result-streaming class /
    a real stop-generating button)."""
    return await any_selector_found(page, selectors.STREAMING_INDICATOR) or await any_selector_found(
        page, selectors.STOP_BUTTON
    )


async def latest_message_has_widget(page: Any) -> bool:
    """True if the latest assistant message contains a rich widget (clock/weather/
    etc card). Such replies can have NO markdown text — rich_assistant_text skips
    widget content — so completion detection can't rely on text alone."""
    try:
        turn = page.locator("[data-testid^='conversation-turn']").last
        if await turn.locator("[data-message-author-role='user']").count() > 0:
            return False  # latest turn is the user's message, not an assistant reply
        return await turn.locator("[class*='WidgetRenderer']").count() > 0
    except Exception:
        return False


async def wait_for_response_complete(
    page: Any,
    *,
    timeout_seconds: int,
    stable_seconds: int,
    idle_timeout_seconds: int = 15,
    hard_timeout_seconds: int | None = None,
    previous_count: int = 0,
    previous_text: str = "",
    previous_image_srcs: list[str] | None = None,
    previous_has_widget: bool = False,
) -> str | None:
    """Wait until the latest assistant reply is complete. Returns the reply text
    (possibly "" for a widget/image-only reply that has no markdown text), or None
    on timeout. The "" vs None distinction lets the caller tell a finished
    media-only reply (deliver the screenshot/image) apart from a real timeout."""
    start = time.monotonic()
    soft_deadline = start + max(0, timeout_seconds)
    hard_deadline = start + max(0, hard_timeout_seconds if hard_timeout_seconds is not None else timeout_seconds)
    last_progress_at = start
    last_progress_signature: tuple[Any, ...] | None = None
    last_text: str | None = None
    stable_for = 0

    while time.monotonic() < hard_deadline:
        now = time.monotonic()
        current_count = await assistant_message_count(page)
        current = await rich_assistant_text(page)
        streaming = await any_selector_found(page, selectors.STREAMING_INDICATOR)
        stop_button = await any_selector_found(page, selectors.STOP_BUTTON)
        generating = streaming or stop_button
        has_widget = await latest_message_has_widget(page)
        current_image_srcs = await generated_image_srcs(page)
        has_image = bool(current_image_srcs)
        image_in_progress = await image_generating(page)

        progress_signature = (
            current_count,
            current,
            streaming,
            stop_button,
            image_in_progress,
            tuple(current_image_srcs),
            has_widget,
        )
        if last_progress_signature is None:
            last_progress_signature = progress_signature
        elif progress_signature != last_progress_signature:
            last_progress_signature = progress_signature
            last_progress_at = now

        # Track stability of the text itself; "" stays stable across iterations too
        # (a widget-only reply keeps yielding "", which is a valid stable state).
        if current == last_text:
            stable_for += 1
        else:
            stable_for = 0
            last_text = current

        # "A NEW reply has arrived." ChatGPT virtualizes the message list (visible
        # node count isn't monotonic) and reuses the page across turns, so compare
        # against the state captured BEFORE sending: text changed, OR a generated
        # image / widget appeared that wasn't there before. We must NOT treat "a
        # generation is in progress" as new — during it the page still shows the
        # PREVIOUS reply, which we'd then wrongly return.
        text_changed = bool(current) and current != previous_text
        new_image = bool(set(current_image_srcs) - set(previous_image_srcs or []))
        new_widget = has_widget and not previous_has_widget
        has_new = current_count > previous_count or text_changed or new_image or new_widget

        # A reply is "ready" once it has text OR a rendered widget OR a generated image.
        content_ready = bool(current) or has_widget or has_image
        # ChatGPT keeps working asynchronously (thinking, searching, generating an
        # image, etc) WITHOUT result-streaming, showing interim status text and/or
        # an image-gen placeholder meanwhile. While either is present the reply
        # isn't done — keep waiting (ignore the interim text) until it clears (and,
        # for images, a NEW src appears). A refusal / plain reply has neither.
        in_progress = (image_in_progress or bool(_INTERIM_RE.search(current or ""))) and not new_image
        if has_new and content_ready and not in_progress and stable_for >= stable_seconds:
            if not generating:
                return current
            # The stop button is the authoritative "still generating" signal: it
            # stays present continuously through a preamble→已思考→answer reply
            # (observed), so while it's there we keep waiting — that is what stops us
            # returning the opening preamble early (the 62-char truncation bug). Only
            # when the stop button is GONE but a residual .result-streaming class
            # lingers do we treat it as stuck and return after grace.
            if not stop_button and stable_for >= stable_seconds + STUCK_GRACE_SECONDS:
                return current

        if now >= soft_deadline:
            idle_window_started = max(last_progress_at, soft_deadline)
            if now - idle_window_started >= max(0, idle_timeout_seconds):
                return None

        if int(now - start) > 0 and int(now - start) % 10 == 0:
            await idle_mouse_movement(page)

        await asyncio.sleep(1)

    return None


def _clean_assistant_text(text: str) -> str:
    cleaned = (text or "").strip()
    prefixes = ("ChatGPT said:", "ChatGPT 说：", "ChatGPT said")
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned
