from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Any

from src.browser import selectors
from src.browser.debug_dump import save_debug_dump
from src.browser.detector import (
    assistant_message_count,
    any_selector_found,
    find_first,
    generated_image_srcs,
    latest_message_has_widget,
    rich_assistant_text,
    wait_for_response_complete,
)
from src.browser.human import hover_and_click, paste_text, random_delay
from src.config import get_settings
from src.utils.errors import ErrorCode, RelayError

# ChatGPT renders rich widgets (clock / weather / stock cards) in a container
# whose class contains "WidgetRenderer" (and is marked not-markdown). Their text
# is noise, so we screenshot them and send as images instead.
WIDGET_SELECTOR = "[class*='WidgetRenderer']"
MAX_WIDGETS_PER_REPLY = 4
# ChatGPT-generated images (e.g. DALL-E) render as <img> with a backend
# estuary/content src — often OUTSIDE the assistant container, and the src needs
# the logged-in session to fetch. detector.generated_image_srcs locates them (by
# rendered size); we download in-page (so cookies apply) and serve via /media.
MAX_IMAGES_PER_REPLY = 4
_FETCH_IMG_B64_JS = """
async (src) => {
  try {
    const r = await fetch(src);
    if (!r.ok) return '';
    const bytes = new Uint8Array(await r.arrayBuffer());
    let bin = '';
    const CH = 8192;
    for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    return btoa(bin);
  } catch (e) {
    return '';
  }
}
"""

# UI/interim noise lines on an image reply (status text, reasoning title, card
# buttons) — dropped so an image reply delivers the picture, not the chrome.
_MEDIA_NOISE_RE = re.compile(
    r"^(正在思考|正在生成.*|.*请稍候。?|Thought for .*|预览|编辑|分享|重试|下载|复制.*|Copy.*)$",
    re.IGNORECASE,
)


def _strip_media_noise(text: str) -> str:
    """Drop interim/UI noise lines — used for image replies whose only real
    content is the picture itself."""
    kept = [
        ln for ln in (text or "").splitlines()
        if ln.strip() and not _MEDIA_NOISE_RE.match(ln.strip())
    ]
    return "\n".join(kept).strip()


def _image_reply_text(answer: str, previous_text: str) -> str:
    """Text to keep for an IMAGE reply.

    An image reply completes the wait on the NEW image src, not on the text
    changing (detector.wait_for_response_complete), so `answer` can still be the
    PREVIOUS turn's reply that the page never updated for this turn (e.g. the
    earlier weather text shown again under a freshly generated picture). After
    dropping UI/interim noise, if what's left merely repeats the pre-send
    snapshot, it isn't this reply's own text — return "" so we deliver just the
    picture."""
    cleaned = _strip_media_noise(answer)
    if cleaned and cleaned == _strip_media_noise(previous_text or ""):
        return ""
    return cleaned


class ChatGPTPage:
    def __init__(self, page: Any, media_store: Any | None = None) -> None:
        self.page = page
        self._media_store = media_store

    async def ask(self, message: str) -> tuple[str, float]:
        settings = get_settings()
        started = time.monotonic()
        try:
            if await any_selector_found(self.page, selectors.LOGIN_INDICATORS):
                raise RelayError(
                    ErrorCode.NOT_LOGGED_IN,
                    "ChatGPT is not logged in. Open noVNC and sign in manually.",
                )

            input_selector = await find_first(self.page, selectors.CHAT_INPUT, visible=True, timeout_ms=2500)
            if not input_selector:
                raise RelayError(
                    ErrorCode.CHAT_INPUT_NOT_FOUND,
                    "Cannot find ChatGPT input box. Open noVNC to inspect the page.",
                )
            previous_assistant_count = await assistant_message_count(self.page)
            previous_assistant_text = await rich_assistant_text(self.page)
            previous_image_srcs = await generated_image_srcs(self.page)
            previous_has_widget = await latest_message_has_widget(self.page)

            await random_delay(settings.before_type_delay_min_ms, settings.before_type_delay_max_ms)
            await paste_text(
                self.page,
                input_selector,
                message,
                delay_min_ms=settings.typing_delay_min_ms,
                delay_max_ms=settings.typing_delay_max_ms,
            )

            send_selector = await find_first(self.page, selectors.SEND_BUTTON, visible=True, timeout_ms=2500)
            if not send_selector:
                raise RelayError(
                    ErrorCode.SEND_BUTTON_NOT_FOUND,
                    "Cannot find ChatGPT send button. Open noVNC to inspect the page.",
                )
            await random_delay(settings.before_send_delay_min_ms, settings.before_send_delay_max_ms)
            await hover_and_click(self.page, send_selector)

            answer = await wait_for_response_complete(
                self.page,
                timeout_seconds=settings.chat_timeout_seconds,
                stable_seconds=settings.response_stable_seconds,
                previous_count=previous_assistant_count,
                previous_text=previous_assistant_text,
                previous_image_srcs=previous_image_srcs,
                previous_has_widget=previous_has_widget,
            )
            if answer is None:
                raise RelayError(
                    ErrorCode.RESPONSE_TIMEOUT,
                    "ChatGPT response did not finish before timeout.",
                )

            # answer may be "" for a widget-only reply (no markdown text). The
            # widget screenshot appended below becomes the actual content, so we
            # only treat the reply as empty AFTER trying to attach the image.
            final_answer = answer.strip()
            prev_srcs = set(previous_image_srcs)
            new_image_srcs = [s for s in await generated_image_srcs(self.page) if s not in prev_srcs]
            if new_image_srcs:
                # An image reply's text is only interim/UI noise, and may still be
                # the PREVIOUS turn's text (the wait completes on the new image
                # src, not on the text changing) — deliver the picture, dropping
                # text that merely repeats the pre-send snapshot.
                final_answer = _image_reply_text(final_answer, previous_assistant_text)
            final_answer = await self._append_media_images(
                final_answer, settings.media_base_url, prev_srcs
            )
            if settings.test_media_url:
                # Manual link-check switch (browser_data/runtime.json).
                final_answer = f"{final_answer}\nMEDIA: {settings.test_media_url}".strip()
            if not final_answer.strip():
                raise RelayError(ErrorCode.RESPONSE_EMPTY, "ChatGPT response is empty.")
            return final_answer, round(time.monotonic() - started, 3)
        except RelayError as exc:
            exc.debug_dir = await save_debug_dump(self.page, exc)
            raise
        except Exception as exc:
            debug_dir = await save_debug_dump(self.page, exc)
            raise RelayError(ErrorCode.UNKNOWN_ERROR, str(exc), debug_dir=debug_dir) from exc

    async def _append_media_images(self, answer: str, media_base_url: str, exclude_image_srcs: set[str]) -> str:
        """Screenshot ChatGPT widgets AND download NEW generated images (e.g.
        DALL-E) in the latest reply, store them, and append 'MEDIA: <url>' tokens
        so OpenClaw forwards them to WeChat. No-op without a media store / base url."""
        base = (media_base_url or "").rstrip("/")
        if self._media_store is None or not base:
            return answer
        result = answer
        tokens = await self._capture_widget_tokens()
        tokens += await self._capture_image_tokens(exclude_image_srcs)
        for token in tokens:
            result = f"{result}\nMEDIA: {base}/media/{token}".strip()
        return result

    async def _capture_widget_tokens(self) -> list[str]:
        tokens: list[str] = []
        try:
            # Anchor on the latest conversation-turn: image/widget replies no longer
            # carry data-message-author-role, so the old selector found nothing.
            assistant = self.page.locator("[data-testid^='conversation-turn']").last
            widgets = assistant.locator(WIDGET_SELECTOR)
            count = await widgets.count()
        except Exception:
            return tokens
        for index in range(min(count, MAX_WIDGETS_PER_REPLY)):
            widget = widgets.nth(index)
            await _wait_widget_rendered(widget)
            png = await _screenshot_widget(self.page, widget)
            if png is None:
                continue
            try:
                tokens.append(self._media_store.put(png, "image/png"))
            except Exception:
                continue
        return tokens

    async def _capture_image_tokens(self, exclude_srcs: set[str]) -> list[str]:
        """Download NEW ChatGPT-generated images (e.g. DALL-E) from the reply via
        an in-page fetch (so the logged-in session/cookies apply — estuary/content
        URLs need it). Skips srcs already present before sending (exclude_srcs) so
        repeated image requests don't re-send the earlier image(s)."""
        tokens: list[str] = []
        srcs = [s for s in await generated_image_srcs(self.page) if s not in exclude_srcs]
        for src in srcs[:MAX_IMAGES_PER_REPLY]:
            try:
                b64 = await self.page.evaluate(_FETCH_IMG_B64_JS, src)
            except Exception:
                continue
            if not b64:
                continue
            try:
                data = base64.b64decode(b64)
            except Exception:
                continue
            if len(data) < 1024:  # too small to be a real generated image
                continue
            try:
                tokens.append(self._media_store.put(data, "image/png"))
            except Exception:
                continue
        return tokens


# Clone the widget with every computed style inlined onto each node so it renders
# faithfully without ChatGPT's global CSS. Styles are read from the live element
# (src, in the document) and written to the detached clone (dst) — getComputedStyle
# only works on attached nodes. setProperty (not cssText) is used so values that
# contain ';' (e.g. data: URIs in background-image) survive. Pseudo-elements
# (::before/::after) and web fonts are not captured — a known, accepted edge.
_INLINE_STYLES_JS = """
(root) => {
  const inlineOne = (src, dst) => {
    const cs = getComputedStyle(src);
    for (let i = 0; i < cs.length; i++) {
      const p = cs[i];
      try { dst.style.setProperty(p, cs.getPropertyValue(p)); } catch (e) {}
    }
  };
  const walk = (src, dst) => {
    if (src.nodeType === 1) inlineOne(src, dst);
    const sc = src.children || [];
    const dc = dst.children || [];
    const n = Math.min(sc.length, dc.length);
    for (let i = 0; i < n; i++) walk(sc[i], dc[i]);
  };
  const clone = root.cloneNode(true);
  walk(root, clone);
  return clone.outerHTML;
}
"""


def _build_render_html(widget_html: str) -> str:
    """Wrap inlined widget HTML in a standalone document: white background, a
    little padding, and an inline-block root that shrinks to the widget so the
    screenshot is tightly cropped."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:#fff}"
        "#webdock-widget-root{display:inline-block;padding:12px;background:#fff}</style>"
        "</head><body>"
        f"<div id='webdock-widget-root'>{widget_html}</div>"
        "</body></html>"
    )


async def _screenshot_widget(page: Any, widget: Any) -> bytes | None:
    """Render a static, self-contained copy of the widget in a throwaway page and
    screenshot that. The widget's outerHTML is cloned with all computed styles
    inlined, then loaded via set_content into a fresh page that has no ChatGPT JS
    — so animations (e.g. a clock's second hand) are frozen at the captured frame
    and there is no scroll/clip misalignment. Returns None on any failure (better
    no image than a wrong one)."""
    try:
        await widget.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        inlined = await widget.evaluate(_INLINE_STYLES_JS)
    except Exception:
        inlined = None
    if not inlined or not isinstance(inlined, str):
        return None
    render_page = None
    try:
        render_page = await page.context.new_page()
        await render_page.set_content(_build_render_html(inlined), wait_until="load")
        root = render_page.locator("#webdock-widget-root")
        return await root.screenshot(timeout=8000)
    except Exception:
        return None
    finally:
        if render_page is not None:
            try:
                await render_page.close()
            except Exception:
                pass


async def _wait_widget_rendered(widget: Any, timeout_seconds: float = 8.0) -> None:
    """Wait until a widget renders content before screenshotting (otherwise we
    capture a blank container). Requires inner_text non-empty for two consecutive
    checks; the text may keep changing (e.g. a live clock), so we only require
    non-empty, not identical."""
    nonempty = 0
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            text = (await widget.inner_text(timeout=2000)).strip()
        except Exception:
            text = ""
        if text:
            nonempty += 1
            if nonempty >= 2:
                return
        else:
            nonempty = 0
        await asyncio.sleep(0.5)
