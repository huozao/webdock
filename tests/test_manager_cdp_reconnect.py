from __future__ import annotations

import asyncio

from src.browser.manager import BrowserManager


class FakeBrowser:
    def __init__(self, connected: bool) -> None:
        self._connected = connected

    def is_connected(self) -> bool:
        return self._connected


class FakeRaisingBrowser:
    def is_connected(self) -> bool:
        raise RuntimeError("connection closed")


def test_started_true_while_cdp_connection_is_live():
    manager = BrowserManager()
    manager._page = object()
    manager._browser = FakeBrowser(connected=True)

    assert manager.started is True


def test_started_false_after_chrome_was_restarted_underneath():
    manager = BrowserManager()
    manager._page = object()
    manager._browser = FakeBrowser(connected=False)

    assert manager.started is False


def test_started_false_when_is_connected_raises():
    manager = BrowserManager()
    manager._page = object()
    manager._browser = FakeRaisingBrowser()

    assert manager.started is False


def test_started_ignores_connection_check_in_managed_mode():
    """launch_persistent_context leaves _browser unset; only the page matters there."""
    manager = BrowserManager()
    manager._page = object()

    assert manager.started is True


class FakeSettings:
    browser_mode = "cdp"

    def ensure_dirs(self) -> None:
        raise RuntimeError("reconnect attempted")


def test_start_drops_stale_handles_before_reconnecting(monkeypatch):
    manager = BrowserManager()
    manager._page = object()
    manager._browser = FakeBrowser(connected=False)
    manager._lane_pages = {"lane": object()}
    monkeypatch.setattr("src.browser.manager.get_settings", FakeSettings)

    async def run() -> str:
        try:
            await manager.start()
        except RuntimeError as exc:
            return str(exc)
        return ""

    # Reaching ensure_dirs proves start() did not early-return on the stale page.
    assert asyncio.run(run()) == "reconnect attempted"
    assert manager._page is None
    assert manager._browser is None
    assert manager._lane_pages == {}
