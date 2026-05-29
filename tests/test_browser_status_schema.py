from fastapi.testclient import TestClient

from src.main import create_app


def test_browser_status_schema_when_browser_not_started():
    client = TestClient(create_app(start_browser=False))

    response = client.get("/browser-status", headers={"Authorization": "Bearer change-me"})

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "ok",
        "browser_started",
        "chrome_running",
        "cdp_attached",
        "chrome_version",
        "current_url",
        "chat_input_found",
        "send_button_found",
        "assistant_message_found",
        "cloudflare_challenge_detected",
        "auth_error_detected",
        "login_status",
        "lanes",
        "last_error",
    }
    assert data["ok"] is True
    assert data["browser_started"] is False
    assert data["cdp_attached"] is False
    assert data["login_status"] in {"chrome_not_running", "not_attached"}
