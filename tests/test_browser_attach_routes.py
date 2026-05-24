from fastapi.testclient import TestClient

from src.main import create_app


class FakeBrowser:
    def __init__(self) -> None:
        self.started = False
        self.start_calls = 0
        self.detach_calls = 0

    async def start(self) -> None:
        self.started = True
        self.start_calls += 1

    async def detach(self) -> None:
        self.started = False
        self.detach_calls += 1

    async def status(self) -> dict[str, object]:
        return {
            "ok": True,
            "browser_started": self.started,
            "chrome_running": True,
            "cdp_attached": self.started,
            "chrome_version": "Chrome/fixture",
            "current_url": "https://chatgpt.com/" if self.started else None,
            "chat_input_found": False,
            "send_button_found": False,
            "assistant_message_found": False,
            "cloudflare_challenge_detected": False,
            "auth_error_detected": False,
            "login_status": "unknown" if self.started else "not_attached",
            "last_error": None,
        }


def test_browser_attach_starts_browser_manager():
    app = create_app(start_browser=False)
    fake_browser = FakeBrowser()
    app.state.browser = fake_browser
    client = TestClient(app)

    response = client.post("/browser/attach", headers={"Authorization": "Bearer change-me"})

    assert response.status_code == 200
    assert fake_browser.start_calls == 1
    assert response.json()["cdp_attached"] is True


def test_browser_detach_releases_browser_manager():
    app = create_app(start_browser=False)
    fake_browser = FakeBrowser()
    fake_browser.started = True
    app.state.browser = fake_browser
    client = TestClient(app)

    response = client.post("/browser/detach", headers={"Authorization": "Bearer change-me"})

    assert response.status_code == 200
    assert fake_browser.detach_calls == 1
    assert response.json()["cdp_attached"] is False


def test_browser_status_can_attach_on_request():
    app = create_app(start_browser=False)
    fake_browser = FakeBrowser()
    app.state.browser = fake_browser
    client = TestClient(app)

    response = client.get("/browser-status?attach=true", headers={"Authorization": "Bearer change-me"})

    assert response.status_code == 200
    assert fake_browser.start_calls == 1
    assert response.json()["cdp_attached"] is True
