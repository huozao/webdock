from __future__ import annotations

import asyncio
import time
from typing import Any

from src.browser import selectors
from src.browser.human import idle_mouse_movement

# If the page still looks "generating" (streaming indicator / stop button) but
# the reply text has been stable this many seconds beyond stable_seconds, return
# anyway. Guards against false "still generating" signals (e.g. a residual
# stop/read-aloud button) that would otherwise hang until timeout.
STUCK_GRACE_SECONDS = 8


# Walk the latest assistant message DOM into WeChat-friendly plain text:
# - tables -> space-aligned text (CJK width aware)
# - lists  -> "• item"
# - drops buttons/svg/role=button (copy / read-aloud UI) so they don't pollute
#   the text or the stability check.
# Pure read (no DOM mutation). Falls back to inner_text if this returns empty.
_RICH_TEXT_JS = r"""
() => {
  const ns = document.querySelectorAll("[data-message-author-role='assistant']");
  const el = ns[ns.length - 1];
  if (!el) return "";
  const root = el.querySelector(".markdown") || el;
  const dw = (s) => { let w = 0; for (const ch of s) { w += (ch.codePointAt(0) > 255 ? 2 : 1); } return w; };
  const pad = (s, n) => s + " ".repeat(Math.max(0, n - dw(s)));
  const SKIP = new Set(["BUTTON", "SVG", "PATH", "USE", "SCRIPT", "STYLE", "IMG"]);
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
      if (tag === "TABLE") { out += "\n" + tableToText(child) + "\n"; continue; }
      if (tag === "BR") { out += "\n"; continue; }
      if (tag === "LI") { out += "\n• " + walk(child).trim(); continue; }
      const inner = walk(child);
      if (BLOCK.has(tag)) { out += "\n" + inner + "\n"; }
      else { out += inner; }
    }
    return out;
  };
  return walk(root).replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
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


async def wait_for_response_complete(
    page: Any,
    *,
    timeout_seconds: int,
    stable_seconds: int,
    previous_count: int = 0,
) -> str:
    start = time.monotonic()
    last_text = ""
    stable_for = 0

    while time.monotonic() - start < timeout_seconds:
        current_count = await assistant_message_count(page)
        current = await rich_assistant_text(page)
        generating = await is_generating(page)
        has_new_assistant = current_count > previous_count

        if current and current == last_text:
            stable_for += 1
        else:
            stable_for = 0
            last_text = current

        if has_new_assistant and current and stable_for >= stable_seconds:
            if not generating:
                return current
            # Completion signal looks stuck (e.g. residual stop/read-aloud button)
            # but text has been stable well past stable_seconds -> return anyway.
            if stable_for >= stable_seconds + STUCK_GRACE_SECONDS:
                return current

        if int(time.monotonic() - start) > 0 and int(time.monotonic() - start) % 10 == 0:
            await idle_mouse_movement(page)

        await asyncio.sleep(1)

    return ""


def _clean_assistant_text(text: str) -> str:
    cleaned = (text or "").strip()
    prefixes = ("ChatGPT said:", "ChatGPT 说：", "ChatGPT said")
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned
