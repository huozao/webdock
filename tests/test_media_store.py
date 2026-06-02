from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.browser.media_store import MediaStore
from src.main import create_app


def test_put_then_get_roundtrip():
    store = MediaStore()
    token = store.put(b"PNGDATA", "image/png")
    assert store.get(token) == (b"PNGDATA", "image/png")
    assert store.get("missing") is None


def test_expired_item_is_dropped():
    store = MediaStore(ttl_seconds=0)
    token = store.put(b"x")
    time.sleep(0.02)
    assert store.get(token) is None


def test_size_cap_evicts_oldest():
    store = MediaStore(max_items=2)
    t1 = store.put(b"1")
    t2 = store.put(b"2")
    t3 = store.put(b"3")
    assert store.get(t1) is None      # oldest evicted
    assert store.get(t2) is not None
    assert store.get(t3) is not None


def test_media_route_serves_image_and_404():
    app = create_app(start_browser=False)
    client = TestClient(app)
    token = app.state.media_store.put(b"PNGBYTES", "image/png")

    ok = client.get(f"/media/{token}")
    assert ok.status_code == 200
    assert ok.content == b"PNGBYTES"
    assert ok.headers["content-type"] == "image/png"

    assert client.get("/media/does-not-exist").status_code == 404
