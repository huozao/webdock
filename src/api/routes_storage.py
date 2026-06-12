from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from src.config import get_settings

router = APIRouter()

ALLOWED_IMAGE_MIMES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
    "image/gif": {".gif"},
}
KEY_RE = re.compile(r"^[a-f0-9]{32}\.(?:jpg|jpeg|png|webp|gif)$")


def _detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _storage_path(key: str) -> Path:
    if not KEY_RE.match(key):
        raise HTTPException(status_code=404, detail="not found")
    base = get_settings().photo_storage_dir
    return base / key


def _metadata_path(key: str) -> Path:
    return _storage_path(key).with_suffix(_storage_path(key).suffix + ".json")


@router.post("/storage/photos")
async def create_photo(file: UploadFile = File(...)) -> dict[str, object]:
    data = await file.read()
    detected_mime = _detect_image_mime(data)
    declared = (file.content_type or "").split(";", 1)[0].strip().lower()
    ext = Path(file.filename or "").suffix.lower()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if detected_mime not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(status_code=400, detail="unsupported file type")
    if ext not in ALLOWED_IMAGE_MIMES[detected_mime]:
        raise HTTPException(status_code=400, detail="file extension does not match image type")
    if declared and declared != detected_mime:
        raise HTTPException(status_code=400, detail="mime type does not match file content")

    key = f"{uuid.uuid4().hex}{ext}"
    path = _storage_path(key)
    path.write_bytes(data)
    _metadata_path(key).write_text(
        json.dumps(
            {
                "content_type": detected_mime,
                "original_filename": file.filename or "",
                "size": len(data),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {"key": key, "content_type": detected_mime, "size": len(data), "url": f"/storage/photos/{key}"}


@router.get("/storage/photos/{key}")
async def get_photo(key: str) -> Response:
    path = _storage_path(key)
    metadata_path = _metadata_path(key)
    if not path.exists() or not metadata_path.exists():
        raise HTTPException(status_code=404, detail="not found")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return Response(
        content=path.read_bytes(),
        media_type=str(metadata.get("content_type") or "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/storage/photos/{key}")
async def delete_photo(key: str) -> dict[str, str]:
    path = _storage_path(key)
    metadata_path = _metadata_path(key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    path.unlink()
    metadata_path.unlink(missing_ok=True)
    return {"status": "ok"}
