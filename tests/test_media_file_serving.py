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


def test_media_route_serves_utf8_filename_without_header_encoding_error():
    app = create_app(start_browser=False)
    client = TestClient(app)
    token = app.state.media_store.put(
        b"PDFDATA",
        "application/pdf",
        filename="和裕达颜色控制标准表_扫描件.pdf",
    )

    response = client.get(f"/media/{token}")

    assert response.status_code == 200
    assert response.content == b"PDFDATA"
    assert response.headers["content-disposition"] == (
        'attachment; filename="download.pdf"; '
        "filename*=UTF-8''%E5%92%8C%E8%A3%95%E8%BE%BE%E9%A2%9C%E8%89%B2"
        "%E6%8E%A7%E5%88%B6%E6%A0%87%E5%87%86%E8%A1%A8_%E6%89%AB%E6%8F%8F"
        "%E4%BB%B6.pdf"
    )
