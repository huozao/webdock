from __future__ import annotations

import asyncio
import base64
import os
import re
import tempfile
import time
from typing import Any
from urllib.parse import quote

from src.browser import selectors
from src.browser.debug_dump import save_debug_dump
from src.browser.detector import (
    assistant_message_count,
    any_selector_found,
    find_first,
    generated_file_targets,
    generated_image_srcs,
    latest_message_has_widget,
    ordered_feishu_markdown,
    SLOT_PLACEHOLDER_RE,
    _strip_markdown_tables,
    rich_assistant_markdown,
    rich_assistant_text,
    wait_for_response_complete,
)
from src.browser.feishu_format import feishu_safe_markdown
from src.browser.file_download import download_chatgpt_file
from src.browser.human import hover_and_click, paste_text, random_delay
from src.browser.image_input import resolve_image_inputs
from src.config import get_settings
from src.utils.errors import ErrorCode, RelayError

# ChatGPT renders rich widgets (clock / weather / stock cards) in a container
# whose class contains "WidgetRenderer" (and is marked not-markdown). Their text
# is noise, so we screenshot them and send as images instead.
WIDGET_SELECTOR = "[class*='WidgetRenderer']"
# Feishu's card renderer flattens GFM pipe tables into run-together text, so on the
# Feishu path we screenshot tables too (and drop their markdown in detector). The
# :not(...) keeps a widget's own inner table from being captured twice. WeChat is
# unchanged (plain text keeps the aligned table).
FEISHU_TABLE_SELECTOR = "table:not([class*='WidgetRenderer'] table)"
MAX_WIDGETS_PER_REPLY = 4


def _media_screenshot_selectors(channel: str) -> list[tuple[str, bool]]:
    """(selector, prefer_clone) pairs to screenshot-and-forward for a reply, per
    channel. prefer_clone=True renders a static clone instead of a live element
    screenshot — needed for tables, whose sticky header/first column render outside
    the <table> box and get cropped by element.screenshot()."""
    if channel == "feishu":
        return [(WIDGET_SELECTOR, False), (FEISHU_TABLE_SELECTOR, True)]
    return [(WIDGET_SELECTOR, False)]
# ChatGPT-generated images (e.g. DALL-E) render as <img> with a backend
# estuary/content src — often OUTSIDE the assistant container, and the src needs
# the logged-in session to fetch. detector.generated_image_srcs locates them (by
# rendered size); we download in-page (so cookies apply) and serve via /media.
MAX_IMAGES_PER_REPLY = 4
MAX_FILES_PER_REPLY = 4

# Inbound file upload: how long to look for the hidden file input, and how long
# to let an attachment finalize on ChatGPT's side before sending the text.
UPLOAD_INPUT_TIMEOUT_MS = 5000
_UPLOAD_DETECT_TIMEOUT_SECONDS = 8.0
_UPLOAD_SETTLE_SECONDS = 2.0
_UPLOAD_FALLBACK_SECONDS = 3.0
# ChatGPT disables the send button while processing document uploads (PDF, DOCX…).
# We poll until it re-enables before sending the message.
_UPLOAD_SEND_READY_TIMEOUT_SECONDS = 60.0
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".avif"})
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
    def __init__(self, page: Any, media_store: Any | None = None, channel: str = "wechat") -> None:
        self.page = page
        self._media_store = media_store
        self._channel = channel

    async def ask(
        self,
        message: str,
        *,
        timeout_seconds: int | None = None,
        hard_timeout_seconds: float | None = None,
    ) -> tuple[str, float]:
        settings = get_settings()
        # Soft timeout is image-aware (passed by the scheduler); the hard timeout is
        # bounded below the bridge's 320s so a slow turn frees the slot in time.
        soft_timeout = timeout_seconds if timeout_seconds is not None else settings.chat_timeout_seconds
        hard_timeout = (
            hard_timeout_seconds
            if hard_timeout_seconds is not None
            else settings.response_hard_timeout_seconds
        )
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
                timeout_seconds=soft_timeout,
                stable_seconds=settings.response_stable_seconds,
                idle_timeout_seconds=settings.response_idle_timeout_seconds,
                hard_timeout_seconds=hard_timeout,
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
            feishu_media_inlined = False
            if new_image_srcs:
                # An image reply's text is only interim/UI noise, and may still be
                # the PREVIOUS turn's text (the wait completes on the new image
                # src, not on the text changing) — deliver the picture, dropping
                # text that merely repeats the pre-send snapshot.
                final_answer = _image_reply_text(final_answer, previous_assistant_text)
            elif self._channel == "feishu":
                # Mixed replies (prose + widgets/tables) are assembled in document
                # order with inline MEDIA placeholders so the bridge can build ONE
                # Feishu card with text and images interleaved as on the web. Only
                # used when the reply actually has screenshot targets; otherwise fall
                # back to the plain structure-preserving markdown (pipe tables, which
                # Feishu can't render, are dropped and re-delivered as screenshots).
                ordered = await self._ordered_feishu_answer(settings.media_base_url)
                if ordered is not None:
                    final_answer = ordered
                    feishu_media_inlined = True
                else:
                    markdown = await rich_assistant_markdown(self.page)
                    if markdown:
                        final_answer = feishu_safe_markdown(_strip_markdown_tables(markdown))
            final_answer = await self._append_media_images(
                final_answer, settings.media_base_url, prev_srcs, capture_widgets=not feishu_media_inlined
            )
            if self._channel == "feishu":
                final_answer = await self._append_generated_files(
                    final_answer, settings.media_base_url, set()
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

    async def _append_media_images(
        self, answer: str, media_base_url: str, exclude_image_srcs: set[str], *, capture_widgets: bool = True
    ) -> str:
        """Screenshot ChatGPT widgets AND download NEW generated images (e.g.
        DALL-E) in the latest reply, store them, and append 'MEDIA: <url>' tokens
        so OpenClaw forwards them. No-op without a media store / base url.
        capture_widgets=False when widgets/tables were already inlined in document
        order (Feishu ordered path) — only NEW generated images are appended then."""
        base = (media_base_url or "").rstrip("/")
        if self._media_store is None or not base:
            return answer
        result = answer
        tokens = await self._capture_widget_tokens() if capture_widgets else []
        tokens += await self._capture_image_tokens(exclude_image_srcs)
        for token in tokens:
            result = f"{result}\nMEDIA: {base}/media/{token}".strip()
        return result

    async def _ordered_feishu_answer(self, media_base_url: str) -> str | None:
        """Assemble a Feishu reply with widgets/tables screenshotted IN PLACE: the
        walk emits markdown with @@WEBDOCK_SLOT_k@@ placeholders at each target's
        document position; here we screenshot each tagged target and swap in a
        'MEDIA: <url>' line. Returns None when there are no targets (caller uses the
        plain markdown path) or no media store."""
        base = (media_base_url or "").rstrip("/")
        if self._media_store is None or not base:
            return None
        text = await ordered_feishu_markdown(self.page)
        if not text or "@@WEBDOCK_SLOT_" not in text:
            return None
        text = feishu_safe_markdown(text)
        try:
            assistant = self.page.locator("[data-testid^='conversation-turn']").last
        except Exception:
            return None
        captured = 0
        for slot in dict.fromkeys(SLOT_PLACEHOLDER_RE.findall(text)):  # dedupe, keep order
            placeholder = f"@@WEBDOCK_SLOT_{slot}@@"
            token = None
            if captured < MAX_WIDGETS_PER_REPLY:
                try:
                    target = assistant.locator(f"[data-webdock-slot='{slot}']").first
                    if await target.count() > 0:
                        prefer_clone = (await target.evaluate("(n) => n.tagName")) == "TABLE"
                        await _wait_widget_rendered(target)
                        png = await _screenshot_widget(self.page, target, prefer_clone=prefer_clone)
                        if png is not None:
                            token = self._media_store.put(png, "image/png")
                            captured += 1
                except Exception:
                    token = None
            replacement = f"\nMEDIA: {base}/media/{token}\n" if token else ""
            text = text.replace(placeholder, replacement)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    async def _capture_widget_tokens(self) -> list[str]:
        tokens: list[str] = []
        try:
            # Anchor on the latest conversation-turn: image/widget replies no longer
            # carry data-message-author-role, so the old selector found nothing.
            assistant = self.page.locator("[data-testid^='conversation-turn']").last
        except Exception:
            return tokens
        # WeChat: widgets only. Feishu: widgets + tables (Feishu can't render tables,
        # so we screenshot them). MAX_WIDGETS_PER_REPLY is a total budget across both.
        for selector, prefer_clone in _media_screenshot_selectors(self._channel):
            if len(tokens) >= MAX_WIDGETS_PER_REPLY:
                break
            try:
                elements = assistant.locator(selector)
                count = await elements.count()
            except Exception:
                continue
            for index in range(count):
                if len(tokens) >= MAX_WIDGETS_PER_REPLY:
                    break
                element = elements.nth(index)
                await _wait_widget_rendered(element)
                png = await _screenshot_widget(self.page, element, prefer_clone=prefer_clone)
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

    async def _append_generated_files(self, answer: str, media_base_url: str, exclude_file_keys: set[str]) -> str:
        """Download ChatGPT-generated files from the latest reply and append
        'FILE: <url>' tokens. Targets are detector-filtered to ChatGPT sandbox/
        generated-file controls, never arbitrary external links."""
        base = (media_base_url or "").rstrip("/")
        if self._media_store is None or not base:
            return answer
        result = answer
        emitted: set[str] = set()
        for target in await generated_file_targets(self.page):
            if target.key in exclude_file_keys or target.key in emitted:
                continue
            file = await download_chatgpt_file(self.page, target)
            if file is None:
                continue
            try:
                token = self._media_store.put(file.data, file.content_type, filename=file.filename)
            except Exception:
                continue
            marker_name = quote(file.filename, safe="._-()")
            marker_mime = quote(file.content_type, safe="/.+-")
            result = f"{result}\nFILE: {base}/media/{token} name={marker_name} mime={marker_mime}".strip()
            emitted.add(target.key)
            if len(emitted) >= MAX_FILES_PER_REPLY:
                break
        return result


# Clone the widget with every computed style inlined onto each node so it renders
# faithfully without ChatGPT's global CSS. Styles are read from the live element
# (src, in the document) and written to the detached clone (dst) — getComputedStyle
# only works on attached nodes. setProperty (not cssText) is used so values that
# contain ';' (e.g. data: URIs in background-image) survive. Pseudo-elements
# (::before/::after) and web fonts are not captured — a known, accepted edge.
_INLINE_STYLES_JS = """
async (root) => {
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
  // Icons (e.g. weather condition images) live at authenticated/blob srcs that a
  // throwaway render page can't load, so they come out broken. Fetch each in THIS
  // page (cookies/same-origin apply) and inline it as a data URI on the clone.
  const srcImgs = [...root.querySelectorAll('img')];
  const dstImgs = [...clone.querySelectorAll('img')];
  await Promise.all(srcImgs.map(async (img, i) => {
    const dst = dstImgs[i];
    if (!dst) return;
    const u = img.currentSrc || img.src;
    if (!u || u.startsWith('data:')) return;
    try {
      const resp = await fetch(u);
      const blob = await resp.blob();
      const dataUrl = await new Promise((res, rej) => {
        const r = new FileReader();
        r.onload = () => res(r.result);
        r.onerror = rej;
        r.readAsDataURL(blob);
      });
      dst.setAttribute('src', dataUrl);
    } catch (e) {}
  }));
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


async def _live_widget_screenshot(widget: Any) -> bytes | None:
    try:
        live = await widget.screenshot(timeout=8000)
        return live or None
    except Exception:
        return None


async def _clone_render_screenshot(page: Any, widget: Any) -> bytes | None:
    """Render a style-inlined static clone of the element in a throwaway page and
    screenshot that. Captures the full element even when the live layout crops it
    (ChatGPT tables freeze the header/first column outside the <table> box)."""
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


async def _screenshot_widget(page: Any, widget: Any, *, prefer_clone: bool = False) -> bytes | None:
    """Screenshot an element. Widgets capture best live (inherited page styles), so
    that is tried first with a clone-render fallback. Tables (prefer_clone=True) must
    go clone-first: element.screenshot() crops their frozen header/first column.
    Returns None on total failure."""
    try:
        await widget.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    if prefer_clone:
        return await _clone_render_screenshot(page, widget) or await _live_widget_screenshot(widget)
    return await _live_widget_screenshot(widget) or await _clone_render_screenshot(page, widget)


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


async def upload_images(page: Any, image_urls: list[str]) -> int:
    """Attach inbound WeChat files (images or documents) to the ChatGPT composer.

    Resolves each URL (base64 data URL or http(s)) to bytes, writes temp files,
    and sets them on ChatGPT's hidden <input type="file">. For document uploads
    (PDF, DOCX, XLSX…) ChatGPT disables the send button while processing; we wait
    for it to re-enable before returning. Best-effort: any failure leaves the turn
    to proceed as text-only. Returns how many files were actually attached."""
    resolved = resolve_image_inputs(image_urls)
    if not resolved:
        return 0
    paths = _write_temp_images(resolved)
    if not paths:
        return 0
    has_documents = any(ext.lower() not in _IMAGE_EXTENSIONS for _, ext in resolved)
    try:
        selector = await find_first(page, selectors.FILE_INPUT, timeout_ms=UPLOAD_INPUT_TIMEOUT_MS)
        if not selector:
            return 0
        await page.set_input_files(selector, paths)
        await _wait_uploads_ready(page, has_documents=has_documents)
        return len(paths)
    finally:
        for path in paths:
            try:
                os.unlink(path)
            except OSError:
                pass


def _write_temp_images(resolved: list[tuple[bytes, str]]) -> list[str]:
    paths: list[str] = []
    for data, ext in resolved:
        try:
            fd, path = tempfile.mkstemp(prefix="webdock-upload-", suffix=ext)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            paths.append(path)
        except OSError:
            continue
    return paths


async def _wait_uploads_ready(page: Any, has_documents: bool = False) -> None:
    """Give the upload time to finalize before sending.

    Phase 1 (all types): wait for an attachment preview chip — quick signal that
    the browser registered the file. Phase 2: for documents ChatGPT disables the
    send button while processing; poll until it re-enables. For images only the
    original short settle is sufficient."""
    deadline = time.monotonic() + _UPLOAD_DETECT_TIMEOUT_SECONDS
    detected = False
    while time.monotonic() < deadline:
        if await any_selector_found(page, selectors.ATTACHMENT_PREVIEW):
            detected = True
            break
        await asyncio.sleep(0.3)
    if has_documents:
        await _wait_send_button_enabled(page)
    else:
        await asyncio.sleep(_UPLOAD_SETTLE_SECONDS if detected else _UPLOAD_FALLBACK_SECONDS)


async def _wait_send_button_enabled(page: Any, timeout_seconds: float = _UPLOAD_SEND_READY_TIMEOUT_SECONDS) -> None:
    """Poll until ChatGPT's send button is not disabled (document processing done)."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            result = await page.evaluate(
                "() => { const b = document.querySelector(\"button[data-testid='send-button']\");"
                " if (!b) return null;"
                " return b.disabled || b.getAttribute('aria-disabled') === 'true'; }"
            )
            if result is False:
                return
        except Exception:
            pass
        await asyncio.sleep(0.5)
