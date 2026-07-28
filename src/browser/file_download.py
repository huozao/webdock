from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)


# Generated images can arrive as a filename pill ("重新发我" replies reference the
# earlier picture as a file instead of re-rendering an <img>), so image extensions
# are first-class download targets too — they're delivered as MEDIA, not FILE.
IMAGE_FILE_EXTENSIONS = frozenset({
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
})
# Everything ChatGPT generates (sandbox / backend-api files — the origin gate in
# _is_chatgpt_generated_href) is downloadable by default. Only formats that
# execute on double-click are refused, so a forwarded file can't become a
# ready-to-run payload in the chat. External third-party links are still never
# auto-downloaded — that boundary is the origin gate, not this list.
BLOCKED_FILE_EXTENSIONS = frozenset({
    ".apk",
    ".bat",
    ".cmd",
    ".com",
    ".dmg",
    ".exe",
    ".jar",
    ".msi",
    ".pif",
    ".scr",
    ".vbs",
})
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
# Clicking a generated-document pill makes ChatGPT's backend materialise the file
# before the browser sees a download event, which can take far longer than the
# 15s this used to allow (2026-07-27: docx replies delivered a bare filename
# because every attempt timed out). Images are unaffected — they keep the short
# wait because their pill opens a preview instead of downloading at all.
DOCUMENT_DOWNLOAD_TIMEOUT_MS = 60000
# ...but the common case is the pill opening the preview flyout, where no download
# event EVER fires, so waiting the full budget burned 60s of the reply's deadline
# for nothing (2026-07-28: a docx turn spent 60 of its 224s here). Give the direct
# download a short first look; if the flyout is up, switch to it immediately, and
# if it isn't, keep waiting out the rest of the budget for a genuinely slow file.
DOCUMENT_PILL_DOWNLOAD_TIMEOUT_MS = 10000
# The document preview flyout ChatGPT opens when a generated-file pill is clicked.
# Its own download control is what actually produces the file. Both the container
# testid and the localized labels are matched — the UI language follows the
# account, and only the label differs between them.
_PREVIEW_FLYOUT_CONTAINERS = (
    "[data-testid='stage-thread-flyout']",
    "[data-testid='screen-threadFlyOut']",
)
_PREVIEW_DOWNLOAD_LABELS = ("Download", "下载")
PREVIEW_DOWNLOAD_BUTTON = ", ".join(
    f"{container} button[aria-label='{label}']"
    for container in _PREVIEW_FLYOUT_CONTAINERS
    for label in _PREVIEW_DOWNLOAD_LABELS
)
PREVIEW_CLOSE_BUTTON = ", ".join(
    f"{container} [data-testid='close-button']" for container in _PREVIEW_FLYOUT_CONTAINERS
)


@dataclass(frozen=True)
class DownloadTarget:
    kind: str
    filename: str
    href: str | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.href or ''}:{self.filename}"


@dataclass(frozen=True)
class DownloadedFile:
    filename: str
    content_type: str
    data: bytes


def parse_download_targets(raw_targets: object) -> list[DownloadTarget]:
    if not isinstance(raw_targets, list):
        return []
    out: list[DownloadTarget] = []
    seen: set[str] = set()
    for raw in raw_targets:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        href = _clean_optional(raw.get("href"))
        filename = _target_filename(raw)
        if not filename:
            continue
        if href:
            # Link targets: anything ChatGPT generated (origin gate) except
            # executable formats.
            if not _is_chatgpt_generated_href(href) or _is_blocked_extension(filename):
                continue
        else:
            # Button targets are labelled with either a filename pill or a
            # localized action ("下载 PDF 扫描件"). Accept on either signal — a
            # bare UI label (copy, reasoning toggle) matches neither, so it's
            # never clicked. The authoritative filename and extension come from
            # the download event (see _download_button).
            if kind != "button":
                continue
            looks_like_file = _has_file_extension(filename) and not _is_blocked_extension(filename)
            if not (looks_like_file or _is_download_intent(filename)):
                continue
        target = DownloadTarget(kind=kind or ("link" if href else "button"), filename=filename, href=href)
        if target.key in seen:
            continue
        seen.add(target.key)
        out.append(target)
    return out


async def download_chatgpt_file(page: object, target: DownloadTarget) -> DownloadedFile | None:
    if target.href:
        return await _download_link(page, target)
    return await _download_button(page, target)


async def _download_link(page: object, target: DownloadTarget) -> DownloadedFile | None:
    try:
        payload = await page.evaluate(_FETCH_DOWNLOAD_B64_JS, target.href)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    b64 = payload.get("data")
    if not isinstance(b64, str) or not b64:
        return None
    try:
        data = base64.b64decode(b64)
    except Exception:
        return None
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        return None
    filename = _safe_filename(str(payload.get("filename") or target.filename)) or target.filename
    content_type = str(payload.get("contentType") or _guess_content_type(filename))
    return DownloadedFile(filename=filename, content_type=content_type, data=data)


# Controls that live OUTSIDE any conversation turn — which is exactly where a
# preview overlay renders. When a file pill opens a preview instead of firing a
# download, this shows whether the overlay carries its own download control.
_OVERLAY_CONTROLS_JS = """
() => {
  const out = [];
  for (const node of document.querySelectorAll("button, [role='button'], a[href], [data-testid]")) {
    if (node.closest("[data-testid^='conversation-turn']")) continue;
    // The sidebar is always outside the turns and would fill the whole budget.
    if (node.closest("nav")) continue;
    if ((node.getAttribute("class") || "").includes("__menu-item")) continue;
    const text = (node.innerText || node.textContent || "").trim();
    const label = node.getAttribute("aria-label") || "";
    if (!text && !label) continue;
    out.push({
      tag: node.tagName,
      cls: (node.getAttribute("class") || "").slice(0, 80),
      testid: node.getAttribute("data-testid") || "",
      label: label.slice(0, 60),
      href: (node.getAttribute("href") || "").slice(0, 100),
      text: text.slice(0, 60),
    });
  }
  return out.slice(0, 50);
}
"""


async def _overlay_controls_debug(page: object) -> list[dict[str, str]]:
    """Diagnostics only — reads the DOM, never clicks."""
    try:
        raw = await page.evaluate(_OVERLAY_CONTROLS_JS)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


async def _download_button(page: object, target: DownloadTarget) -> DownloadedFile | None:
    # Image pills open a preview instead of downloading (fallback below), so
    # don't pay the full download wait before capturing the preview.
    is_image = Path(target.filename).suffix.lower() in IMAGE_FILE_EXTENSIONS
    try:
        async with page.expect_download(
            timeout=4000 if is_image else DOCUMENT_PILL_DOWNLOAD_TIMEOUT_MS
        ) as download_info:
            await page.locator("button").filter(has_text=target.filename).first.click(timeout=5000)
        download = await download_info.value
    except Exception as exc:
        log.warning(
            "file pill click did not produce a download: %s: %s (file=%r)",
            type(exc).__name__,
            str(exc).splitlines()[0][:200] if str(exc) else "",
            target.filename,
        )
        # An image filename pill opens a preview overlay instead of firing a
        # download event (observed 2026-07-18) — capture the previewed image.
        if is_image:
            return await _capture_preview_image(page, target)
        # A document pill opens ChatGPT's preview flyout (observed 2026-07-27) —
        # the flyout carries its own download control.
        if await _preview_flyout_visible(page):
            return await _download_from_preview_flyout(page, target)
        # No flyout: this pill is a direct download that is merely slow, so spend
        # the rest of the original budget on the event we already armed the click for.
        pending = await _await_pending_download(
            page, DOCUMENT_DOWNLOAD_TIMEOUT_MS - DOCUMENT_PILL_DOWNLOAD_TIMEOUT_MS
        )
        if pending is not None:
            return await _read_download(pending, target)
        return await _download_from_preview_flyout(page, target)
    return await _read_download(download, target)


async def _preview_flyout_visible(page: object) -> bool:
    """Is ChatGPT's document preview flyout on screen? Decides whether a pill click
    that produced no download opened the preview (use its own control) or is just a
    slow direct download (keep waiting)."""
    for container in _PREVIEW_FLYOUT_CONTAINERS:
        try:
            if await page.locator(container).first.is_visible(timeout=1000):
                return True
        except Exception:
            continue
    return False


async def _await_pending_download(page: object, timeout_ms: int) -> object | None:
    """Wait out the remaining download budget after the short first look expired.
    The click already happened, so a late download event still lands here."""
    if timeout_ms <= 0:
        return None
    try:
        return await page.wait_for_event("download", timeout=timeout_ms)
    except Exception:
        return None


async def _download_from_preview_flyout(
    page: object, target: DownloadTarget
) -> DownloadedFile | None:
    """Use the preview flyout's own Download control, then close the flyout.

    Clicking a generated-document pill opens ChatGPT's document preview instead of
    downloading, so the click resolves but no download event ever fires. The
    flyout is left open if we don't dismiss it, which would render the next reply
    behind it."""
    try:
        async with page.expect_download(timeout=DOCUMENT_DOWNLOAD_TIMEOUT_MS) as download_info:
            await page.locator(PREVIEW_DOWNLOAD_BUTTON).first.click(timeout=5000)
        download = await download_info.value
    except Exception as exc:
        log.warning(
            "preview flyout download failed: %s: %s (file=%r) overlay=%s",
            type(exc).__name__,
            str(exc).splitlines()[0][:200] if str(exc) else "",
            target.filename,
            await _overlay_controls_debug(page),
        )
        await _close_preview_flyout(page)
        return None
    await _close_preview_flyout(page)
    return await _read_download(download, target)


async def _close_preview_flyout(page: object) -> None:
    """Escape does NOT dismiss this flyout (verified 2026-07-27 — the layer stayed
    at width 751 through repeated Escapes, wedging every following turn with
    RESPONSE_TIMEOUT). It carries its own close control; the keypress is kept only
    as a fallback for a reshaped UI."""
    try:
        await page.locator(PREVIEW_CLOSE_BUTTON).first.click(timeout=3000)
        return
    except Exception:
        pass
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


# Presence of the container is not enough — it stays in the DOM after the flyout
# closes. Only a rendered box (non-zero width) means it is actually covering the
# thread. Returns the flyout's own controls too, so a close affordance can be
# found when Escape doesn't dismiss it.
_FLYOUT_STATE_JS = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return null;
  const controls = [];
  for (const node of el.querySelectorAll("button, [role='button']")) {
    controls.push({
      testid: node.getAttribute("data-testid") || "",
      label: (node.getAttribute("aria-label") || "").slice(0, 40),
      text: (node.innerText || node.textContent || "").trim().slice(0, 30),
    });
  }
  return {width: Math.round(rect.width), controls: controls.slice(0, 20)};
}
"""


async def dismiss_stale_preview_flyout(page: object) -> bool:
    """Close a document preview flyout left open from an earlier turn.

    A flyout that outlives its turn covers the thread and the next reply never
    reaches a completion signal — the turn dies with RESPONSE_TIMEOUT even though
    the message sent fine. The download paths dismiss their own flyout, but a turn
    that fails before reaching them (or anything opened by hand in noVNC) would
    otherwise wedge every following request, so each turn starts by clearing one.
    """
    state = None
    for container in _PREVIEW_FLYOUT_CONTAINERS:
        try:
            state = await page.evaluate(_FLYOUT_STATE_JS, container)
        except Exception:
            return False
        if state:
            break
    if not state:
        return False
    log.warning("dismissing a stale document preview flyout: %s", state)
    await _close_preview_flyout(page)
    return True


async def _read_download(download: object, target: DownloadTarget) -> DownloadedFile | None:
    try:
        path = await download.path()
    except Exception:
        return None
    if not path:
        log.warning("download event fired but produced no path (file=%r)", target.filename)
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        return None
    filename = _safe_filename(getattr(download, "suggested_filename", "") or target.filename) or target.filename
    if _is_blocked_extension(filename):
        # The real download decides the type; never forward an executable format
        # even if the button label looked like a harmless download.
        return None
    return DownloadedFile(filename=filename, content_type=_guess_content_type(filename), data=data)


# The preview overlay renders the file's backend image OUTSIDE any conversation
# turn at a real size — that distinguishes it from the in-chat reply images.
_PREVIEW_IMAGE_SRC_JS = """
() => {
  for (const im of document.querySelectorAll("img")) {
    if (im.closest("[data-testid^='conversation-turn']")) continue;
    const src = im.currentSrc || im.src || "";
    if (!/backend-api\\/(estuary|files)\\/|oaiusercontent/.test(src)) continue;
    if (im.clientWidth >= 300 && im.clientHeight >= 300) return src;
  }
  return null;
}
"""
# In-page fetch so the logged-in session cookies apply (estuary URLs need them).
_FETCH_PREVIEW_B64_JS = """
async (src) => {
  try {
    const res = await fetch(src, { credentials: "include" });
    if (!res.ok) return null;
    const bytes = new Uint8Array(await res.arrayBuffer());
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  } catch (e) {
    return null;
  }
}
"""


async def _capture_preview_image(page: object, target: DownloadTarget) -> DownloadedFile | None:
    """Grab the image shown by the file pill's preview overlay, then close it."""
    src = None
    for _ in range(10):  # the overlay renders async after the click
        try:
            src = await page.evaluate(_PREVIEW_IMAGE_SRC_JS)
        except Exception:
            src = None
        if src:
            break
        await asyncio.sleep(0.5)
    data = None
    if src:
        try:
            b64 = await page.evaluate(_FETCH_PREVIEW_B64_JS, src)
            data = base64.b64decode(b64) if b64 else None
        except Exception:
            data = None
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    if not data or len(data) < 1024 or len(data) > MAX_DOWNLOAD_BYTES:
        return None
    return DownloadedFile(
        filename=target.filename, content_type=_guess_content_type(target.filename), data=data
    )


def _target_filename(raw: dict[str, object]) -> str | None:
    for key in ("download", "filename", "text"):
        filename = _safe_filename(_clean_optional(raw.get(key)))
        if filename:
            return filename
    href = _clean_optional(raw.get("href"))
    if not href:
        return None
    parsed = urlparse(href)
    name = Path(unquote(parsed.path)).name
    return _safe_filename(name)


def _is_chatgpt_generated_href(href: str) -> bool:
    """ChatGPT-generated file URLs come in three known shapes:
    - legacy code-interpreter links: sandbox:/mnt/data/... (or bare /mnt/data/)
    - legacy file downloads: chatgpt.com/backend-api/files/<id>/download
    - the current file service (images AND documents, observed 2026-07-18):
      chatgpt.com/backend-api/estuary/content?id=file_...&ts=...&sig=...
    oaiusercontent.com is OpenAI's signed user-content CDN (same trust class).
    Anything else is a third-party link and is never auto-downloaded."""
    if href.startswith("sandbox:/mnt/data/") or href.startswith("/mnt/data/"):
        return True
    parsed = urlparse(href)
    path = parsed.path or href
    host = (parsed.netloc or "").lower()
    if host.endswith("oaiusercontent.com"):
        return True
    if host and not host.endswith("chatgpt.com"):
        return False
    if "/backend-api/files/" in path and path.rstrip("/").endswith("/download"):
        return True
    return "/backend-api/estuary/content" in path and "id=file" in (parsed.query or "")


# A real filename suffix (".pdf", ".json") — not a stray dot inside a sentence
# label ("v2.0 说明" must not read as a file).
_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def _has_file_extension(filename: str) -> bool:
    return bool(_FILE_EXTENSION_RE.search(filename.strip()))


def _is_blocked_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in BLOCKED_FILE_EXTENSIONS


# ChatGPT labels a generated-file button with a localized action, not a filename
# (e.g. "下载 PDF 扫描件", "Download the report"). Match download intent so we only
# click real download affordances and never pay an expect_download timeout on an
# unrelated inline button.
_DOWNLOAD_INTENT_RE = re.compile(
    r"下载|下載|导出|導出|另存|保存|download|export"
    r"|\b(?:pdf|word|excel|csv|pptx?|docx?|xlsx?|txt)\b"
    r"|文档|文檔|表格|文件|附件",
    re.IGNORECASE,
)


def _is_download_intent(label: str | None) -> bool:
    return bool(label and _DOWNLOAD_INTENT_RE.search(label))


def _safe_filename(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = Path(value.replace("\\", "/")).name.strip().strip('"')
    if not cleaned or cleaned in {".", ".."}:
        return None
    return cleaned.replace("\r", "_").replace("\n", "_")


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


_FETCH_DOWNLOAD_B64_JS = """
async (href) => {
  try {
    const r = await fetch(href);
    if (!r.ok) return null;
    const len = Number(r.headers.get('content-length') || '0');
    if (len > 26214400) return null;
    const bytes = new Uint8Array(await r.arrayBuffer());
    if (bytes.length > 26214400) return null;
    let bin = '';
    const CH = 8192;
    for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    const cd = r.headers.get('content-disposition') || '';
    const m = cd.match(/filename\\*?=(?:UTF-8''|")?([^";]+)/i);
    return {
      data: btoa(bin),
      contentType: (r.headers.get('content-type') || '').split(';')[0] || '',
      filename: m ? decodeURIComponent(m[1].replace(/"/g, '')) : ''
    };
  } catch (e) {
    return null;
  }
}
"""
