from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()


@router.get("/media/{token}")
async def get_media(token: str, request: Request) -> Response:
    """Serve a cached image (screenshot of a ChatGPT widget) so OpenClaw can
    download it and forward to WeChat. Unauthenticated by design (the bearer
    middleware excludes /media); tokens are random and short-lived."""
    store = getattr(request.app.state, "media_store", None)
    item = store.get(token) if store is not None else None
    if item is None:
        return JSONResponse(status_code=404, content={"error": "media not found or expired"})
    data, content_type = item
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "no-store"})
