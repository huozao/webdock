from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


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
})
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
    try:
        async with page.expect_download(timeout=15000) as download_info:
            await page.locator("button").filter(has_text=target.filename).first.click(timeout=5000)
        download = await download_info.value
        path = await download.path()
    except Exception:
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
