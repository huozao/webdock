from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()


def _content_disposition(filename: str) -> str:
    safe_filename = (
        filename.replace("\\", "_")
        .replace("/", "_")
        .replace('"', "_")
        .replace("\r", "_")
        .replace("\n", "_")
    )
    try:
        safe_filename.encode("ascii")
    except UnicodeEncodeError:
        extension = ""
        if "." in safe_filename:
            candidate = safe_filename.rsplit(".", 1)[-1]
            if candidate.isascii() and candidate.isalnum() and len(candidate) <= 10:
                extension = "." + candidate
        encoded = quote(safe_filename, safe="")
        return f'attachment; filename="download{extension}"; filename*=UTF-8\'\'{encoded}'
    return f'attachment; filename="{safe_filename}"'


@router.get("/media/{token}")
async def get_media(token: str, request: Request) -> Response:
    """Serve a cached image (screenshot of a ChatGPT widget) so OpenClaw can
    download it and forward to WeChat. Unauthenticated by design (the bearer
    middleware excludes /media); tokens are random and short-lived."""
    store = getattr(request.app.state, "media_store", None)
    item = store.get(token) if store is not None else None
    if item is None:
        return JSONResponse(status_code=404, content={"error": "media not found or expired"})
    headers = {"Cache-Control": "no-store"}
    if item.filename:
        headers["Content-Disposition"] = _content_disposition(item.filename)
    return Response(content=item.data, media_type=item.content_type, headers=headers)
