from __future__ import annotations

from fastapi.testclient import TestClient

from src.config import get_settings
from src.main import create_app


def test_persistent_photo_storage_requires_token_and_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "storage-token")
    monkeypatch.setenv("PHOTO_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()

    app = create_app(start_browser=False)
    client = TestClient(app)

    unauth = client.post(
        "/storage/photos",
        files={"file": ("memory.png", b"\x89PNG\r\n\x1a\nphoto", "image/png")},
    )
    assert unauth.status_code == 401

    auth = {"Authorization": "Bearer storage-token"}
    created = client.post(
        "/storage/photos",
        headers=auth,
        files={"file": ("memory.png", b"\x89PNG\r\n\x1a\nphoto", "image/png")},
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["key"].endswith(".png")
    assert payload["content_type"] == "image/png"
    assert payload["size"] == len(b"\x89PNG\r\n\x1a\nphoto")

    stored = client.get(f"/storage/photos/{payload['key']}", headers=auth)
    assert stored.status_code == 200
    assert stored.content == b"\x89PNG\r\n\x1a\nphoto"
    assert stored.headers["content-type"] == "image/png"

    deleted = client.delete(f"/storage/photos/{payload['key']}", headers=auth)
    assert deleted.status_code == 200
    assert client.get(f"/storage/photos/{payload['key']}", headers=auth).status_code == 404

    get_settings.cache_clear()
