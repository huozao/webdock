from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


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
ALLOWED_GENERATED_FILE_EXTENSIONS = frozenset({
    ".csv",
    ".doc",
    ".docx",
    ".pdf",
    ".ppt",
    ".pptx",
    ".txt",
    ".xls",
    ".xlsx",
}) | IMAGE_FILE_EXTENSIONS
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


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
            # Link targets carry a real filename in the URL/label.
            if not _is_chatgpt_generated_href(href) or not _has_allowed_extension(filename):
                continue
        else:
            # Button targets are labelled with a localized action ("下载 PDF 扫描件"),
            # not a filename. Accept on download intent; the authoritative filename
            # and extension come from the download event (see _download_button).
            if kind != "button":
                continue
            if not (_has_allowed_extension(filename) or _is_download_intent(filename)):
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


async def _download_button(page: object, target: DownloadTarget) -> DownloadedFile | None:
    # Image pills open a preview instead of downloading (fallback below), so
    # don't pay the full download wait before capturing the preview.
    is_image = Path(target.filename).suffix.lower() in IMAGE_FILE_EXTENSIONS
    try:
        async with page.expect_download(timeout=4000 if is_image else 15000) as download_info:
            await page.locator("button").filter(has_text=target.filename).first.click(timeout=5000)
        download = await download_info.value
        path = await download.path()
    except Exception:
        # An image filename pill opens a preview overlay instead of firing a
        # download event (observed 2026-07-18) — capture the previewed image.
        if is_image:
            return await _capture_preview_image(page, target)
        return None
    if not path:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        return None
    filename = _safe_filename(getattr(download, "suggested_filename", "") or target.filename) or target.filename
    if not _has_allowed_extension(filename):
        # The real download decides the type; never forward a non-whitelisted file
        # even if the button label looked like a download.
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
    if href.startswith("sandbox:/mnt/data/") or href.startswith("/mnt/data/"):
        return True
    parsed = urlparse(href)
    path = parsed.path or href
    host = (parsed.netloc or "").lower()
    if host and not host.endswith("chatgpt.com"):
        return False
    return "/backend-api/files/" in path and path.rstrip("/").endswith("/download")


def _has_allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_GENERATED_FILE_EXTENSIONS


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
