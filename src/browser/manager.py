from __future__ import annotations

import logging
import os
import random
import socket
import subprocess
import time
import json
import urllib.request
from pathlib import Path
from typing import Any

from src.browser import selectors
from src.browser.detector import any_selector_found
from src.browser.stealth import apply_stealth_if_enabled
from src.config import Settings
from src.config import get_settings

log = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self.last_error: str | None = None

    @property
    def started(self) -> bool:
        return self._page is not None

    @property
    def page(self) -> Any | None:
        return self._page

    async def start(self) -> None:
        if self.started:
            return
        settings = get_settings()
        settings.ensure_dirs()
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        if is_cdp_mode(settings.browser_mode):
            await self._start_cdp(settings)
            return

        _kill_orphan_chrome_processes()
        _cleanup_stale_locks(settings.browser_profile_dir)

        await self._start_managed_browser(settings)

    async def _start_cdp(self, settings: Settings) -> None:
        self._browser = await self._connect_over_cdp_with_retry(settings)
        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        self._page = _choose_page(self._context.pages)
        if self._page is None:
            self._page = await self._context.new_page()
        if should_navigate_to_chatgpt(self._page.url):
            await self._page.goto(settings.chatgpt_url, wait_until="domcontentloaded")

    async def _connect_over_cdp_with_retry(self, settings: Settings) -> Any:
        deadline = time.monotonic() + settings.cdp_connect_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return await self._playwright.chromium.connect_over_cdp(settings.cdp_url)
            except Exception as exc:
                last_error = exc
                await _sleep_one_second()
        raise RuntimeError(f"Cannot connect to Chrome CDP at {settings.cdp_url}: {last_error}")

    async def _start_managed_browser(self, settings: Settings) -> None:
        launch_kwargs = {
            "user_data_dir": str(settings.browser_profile_dir),
            "headless": False,
            "slow_mo": settings.slow_mo_ms,
            "viewport": jitter_viewport(
                settings.viewport_width,
                settings.viewport_height,
                settings.viewport_jitter_px,
            ),
            "args": build_chromium_args(settings),
        }
        if settings.browser_channel:
            launch_kwargs["channel"] = settings.browser_channel

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception:
            if settings.browser_channel:
                log.warning("Browser channel %s failed; falling back to bundled chromium", settings.browser_channel)
                launch_kwargs.pop("channel", None)
                self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
            else:
                raise
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.goto(settings.chatgpt_url, wait_until="domcontentloaded")
        await apply_stealth_if_enabled(self._context, enabled=settings.enable_stealth)

    async def stop(self) -> None:
        try:
            settings = get_settings()
            if self._context and not is_cdp_mode(settings.browser_mode):
                await self._context.close()
            if self._browser and not is_cdp_mode(settings.browser_mode):
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        finally:
            self._browser = None
            self._context = None
            self._playwright = None
            self._page = None

    async def detach(self) -> None:
        await self.stop()

    async def status(self) -> dict[str, Any]:
        settings = get_settings()
        chrome_info = await _cdp_endpoint_info(settings) if is_cdp_mode(settings.browser_mode) else {}
        chrome_running = self.started or bool(chrome_info)
        chrome_version = chrome_info.get("Browser")
        cdp_attached = self.started
        page = self._page
        if page is None:
            return {
                "ok": True,
                "browser_started": False,
                "chrome_running": chrome_running,
                "cdp_attached": False,
                "chrome_version": chrome_version,
                "current_url": None,
                "chat_input_found": False,
                "send_button_found": False,
                "assistant_message_found": False,
                "cloudflare_challenge_detected": False,
                "auth_error_detected": False,
                "login_status": "not_attached" if chrome_running else "chrome_not_running",
                "last_error": self.last_error,
            }

        title = await _safe_page_title(page)
        chat_input_found = await any_selector_found(page, selectors.CHAT_INPUT)
        send_button_found = await any_selector_found(page, selectors.SEND_BUTTON)
        assistant_message_found = await any_selector_found(page, selectors.ASSISTANT_MESSAGE)
        login_indicator_found = await any_selector_found(page, selectors.LOGIN_INDICATORS)
        cloudflare_challenge_detected = detect_cloudflare_challenge(page.url, title)
        auth_error_detected = detect_auth_error(page.url, title)

        login_status = classify_login_status(chat_input_found, login_indicator_found, auth_error_detected)

        return {
            "ok": True,
            "browser_started": True,
            "chrome_running": chrome_running,
            "cdp_attached": cdp_attached,
            "chrome_version": chrome_version,
            "current_url": page.url,
            "chat_input_found": chat_input_found,
            "send_button_found": send_button_found,
            "assistant_message_found": assistant_message_found,
            "cloudflare_challenge_detected": cloudflare_challenge_detected,
            "auth_error_detected": auth_error_detected,
            "login_status": login_status,
            "last_error": self.last_error,
        }


def build_chromium_args(settings: Settings) -> list[str]:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
    ]
    resolver_rules = _resolve_domains_for_chrome()
    if resolver_rules:
        args.append(f"--host-resolver-rules={resolver_rules}")
    return args


def classify_login_status(
    chat_input_found: bool,
    login_indicator_found: bool,
    auth_error_detected: bool = False,
) -> str:
    if auth_error_detected:
        return "auth_error"
    if login_indicator_found:
        return "not_logged_in"
    if chat_input_found:
        return "probably_logged_in"
    return "unknown"


def is_cdp_mode(browser_mode: str) -> bool:
    return browser_mode.lower() in {"ecs_cdp", "cdp"}


def should_navigate_to_chatgpt(current_url: str | None) -> bool:
    if not current_url or current_url == "about:blank":
        return True
    return "chatgpt.com" not in current_url


def detect_cloudflare_challenge(url: str | None, title: str | None) -> bool:
    haystack = f"{url or ''}\n{title or ''}".lower()
    return any(
        marker in haystack
        for marker in (
            "just a moment",
            "challenge-platform",
            "cloudflare",
            "verify you are human",
        )
    )


def detect_auth_error(url: str | None, title: str | None) -> bool:
    haystack = f"{url or ''}\n{title or ''}".lower()
    return "/api/auth/error" in haystack or "auth/error" in haystack


def _choose_page(pages: list[Any]) -> Any | None:
    if not pages:
        return None
    for page in pages:
        try:
            if "chatgpt.com" in page.url:
                return page
        except Exception:
            continue
    return pages[0]


async def _sleep_one_second() -> None:
    import asyncio

    await asyncio.sleep(1)


async def _safe_page_title(page: Any) -> str:
    try:
        return await page.title()
    except Exception:
        return ""


async def _cdp_endpoint_info(settings: Settings) -> dict[str, Any]:
    import asyncio

    if not is_cdp_mode(settings.browser_mode):
        return {}
    return await asyncio.to_thread(_fetch_cdp_endpoint_info, settings.cdp_url)


def _fetch_cdp_endpoint_info(cdp_url: str) -> dict[str, Any]:
    url = cdp_url.rstrip("/") + "/json/version"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=0.5) as response:
            payload = response.read().decode("utf-8")
    except Exception:
        return {}
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def jitter_viewport(width: int, height: int, jitter: int) -> dict[str, int]:
    if jitter <= 0:
        return {"width": width, "height": height}
    return {
        "width": width + random.randint(-jitter, jitter),
        "height": height + random.randint(-jitter, jitter),
    }


def _cleanup_stale_locks(profile_dir: Path) -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = profile_dir / name
        try:
            if path.exists():
                path.unlink()
        except Exception as exc:
            log.warning("Cannot remove stale Chromium lock %s: %s", path, exc)

    for pattern in ("**/*-journal", "**/*-wal", "**/*-shm"):
        for path in profile_dir.glob(pattern):
            try:
                path.unlink()
            except Exception:
                pass


def _kill_orphan_chrome_processes() -> None:
    if os.name == "nt":
        return
    for pattern in ("chrome-for-testing", "chromium"):
        try:
            subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=3, check=False)
        except Exception:
            pass


def _resolve_domains_for_chrome() -> str:
    if not os.path.exists("/.dockerenv") and os.environ.get("DISPLAY") != ":99":
        return ""

    domains = [
        "chatgpt.com",
        "cdn.oaistatic.com",
        "ab.chatgpt.com",
        "auth.openai.com",
        "auth0.openai.com",
        "challenges.cloudflare.com",
        "static.cloudflareinsights.com",
    ]
    rules = []
    for domain in domains:
        try:
            rules.append(f"MAP {domain} {socket.gethostbyname(domain)}")
        except Exception:
            continue
    return ", ".join(rules)
