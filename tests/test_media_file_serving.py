from __future__ import annotations

from fastapi.testclient import TestClient

from src.browser.media_store import MediaStore
from src.main import create_app


def test_media_store_keeps_optional_filename():
    store = MediaStore()

    token = store.put(b"PDFDATA", "application/pdf", filename="report.pdf")

    item = store.get(token)
    assert item is not None
    assert item.data == b"PDFDATA"
    assert item.content_type == "application/pdf"
    assert item.filename == "report.pdf"


def test_media_route_serves_file_download_headers():
    app = create_app(start_browser=False)
    client = TestClient(app)
    token = app.state.media_store.put(b"hello", "text/plain", filename="answer.txt")

    response = client.get(f"/media/{token}")

    assert response.status_code == 200
    assert response.content == b"hello"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="answer.txt"'
