from __future__ import annotations

import base64
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

# Cap inbound images per turn (mirrors the output cap in chatgpt_page) and the
# size of each, so a malformed/huge data URL can't exhaust memory.
MAX_INPUT_IMAGES = 4
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_DOWNLOAD_TIMEOUT_SECONDS = 20

# data:[<mime>][;base64],<payload>
_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)?[^,]*?(?P<b64>;base64)?,(?P<data>.*)$",
    re.DOTALL | re.IGNORECASE,
)
_EXT_BY_MIME = {
    # Images
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/avif": ".avif",
    # Binary documents (ChatGPT-supported)
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/zip": ".zip",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "application/json": ".json",
}


def extract_image_urls(content: Any) -> list[str]:
    """Pull image URLs (data: or http(s)) from an OpenAI vision message content.

    An image part is the standard ``{"type": "image_url", "image_url": {"url":
    ...}}``; ``{"image_url": "<url>"}`` (url given directly) is also tolerated.
    Plain-string / text-only content yields no images.
    """
    if not isinstance(content, list):
        return []
    urls: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        image_url = item.get("image_url")
        if image_url is None:
            continue
        url = image_url.get("url") if isinstance(image_url, dict) else image_url
        if isinstance(url, str) and url.strip():
            urls.append(url.strip())
    return urls[:MAX_INPUT_IMAGES]


def resolve_image_inputs(urls: list[str]) -> list[tuple[bytes, str]]:
    """Resolve image URLs to ``(bytes, extension)`` pairs, skipping any that fail."""
    resolved: list[tuple[bytes, str]] = []
    for url in urls[:MAX_INPUT_IMAGES]:
        item = resolve_image(url)
        if item is not None:
            resolved.append(item)
    return resolved


def resolve_image(url: str) -> tuple[bytes, str] | None:
    """Resolve one image URL to ``(bytes, extension)``.

    Handles base64 ``data:`` URLs (decoded in-process) and ``http(s)`` URLs
    (downloaded server-side). Returns None on any failure so the caller can drop
    that image and still send the rest of the turn.
    """
    try:
        if url.startswith("data:"):
            return _decode_data_url(url)
        if url.startswith(("http://", "https://")):
            return _download(url)
    except Exception as exc:  # never let a bad image abort the chat
        log.warning("Cannot resolve inbound image: %s", exc)
    return None


def _decode_data_url(url: str) -> tuple[bytes, str] | None:
    match = _DATA_URL_RE.match(url)
    if not match:
        return None
    raw = match.group("data")
    if match.group("b64"):
        data = base64.b64decode(raw, validate=False)
    else:
        data = urllib.parse.unquote_to_bytes(raw)
    return _sized(data, match.group("mime"))


def _download(url: str) -> tuple[bytes, str] | None:
    request = urllib.request.Request(url, headers={"User-Agent": "webdock/1.0"})
    with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
        data = response.read(MAX_IMAGE_BYTES + 1)
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip() or None
    return _sized(data, content_type)


def _sized(data: bytes, mime: str | None) -> tuple[bytes, str] | None:
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    return data, _ext_for(data, mime)


def _ext_for(data: bytes, mime: str | None) -> str:
    if mime:
        ext = _EXT_BY_MIME.get(mime.lower())
        if ext:
            return ext
    # Fall back to magic bytes when the mime is missing or unknown.
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"
