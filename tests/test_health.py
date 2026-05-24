from fastapi.testclient import TestClient

from src.main import create_app


def test_healthz_returns_expected_structure():
    client = TestClient(create_app(start_browser=False))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "webdock"}
