from src.browser.manager import is_cdp_mode, should_navigate_to_chatgpt
from src.config import Settings


def test_settings_default_to_ecs_cdp_browser():
    settings = Settings()

    assert settings.browser_mode == "ecs_cdp"
    assert settings.cdp_url == "http://127.0.0.1:9222"
    assert settings.attach_on_start is False


def test_cdp_mode_aliases():
    assert is_cdp_mode("ecs_cdp") is True
    assert is_cdp_mode("cdp") is True
    assert is_cdp_mode("playwright_managed") is False


def test_chatgpt_auth_pages_are_not_overwritten_on_startup():
    assert should_navigate_to_chatgpt("https://chatgpt.com/api/auth/error") is False
    assert should_navigate_to_chatgpt("https://chatgpt.com/") is False
    assert should_navigate_to_chatgpt("about:blank") is True
    assert should_navigate_to_chatgpt("https://example.com/") is True
