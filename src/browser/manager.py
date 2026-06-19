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
from src.browser.lane_scheduler import LaneContext
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
        self._lane_pages: dict[str, Any] = {}
        self._lane_contexts: dict[str, LaneContext] = {}
        self.last_error: str | None = None

    @property
    def started(self) -> bool:
        return self._page is not None

    @property
    def page(self) -> Any | None:
        return self._page

    async def page_for_lane(self, lane: LaneContext) -> Any:
        if self._context is None:
            return self._page

        existing = self._lane_pages.get(lane.key)
        if existing is not None and not _is_page_closed(existing):
            return existing

        adopted = await self._adopt_matching_page(lane)
        if adopted is not None:
            self._lane_pages[lane.key] = adopted
            self._lane_contexts[lane.key] = lane
            return adopted

        page = self._page if not self._lane_pages and self._page is not None else await self._context.new_page()
        await _navigate_lane_page(page, lane, get_settings())
        self._lane_pages[lane.key] = page
        self._lane_contexts[lane.key] = lane
        return page

    async def reset_lane_page(self, lane: LaneContext) -> Any:
        if self._context is None:
            return self._page

        existing = self._lane_pages.pop(lane.key, None)
        self._lane_contexts.pop(lane.key, None)
        if existing is not None and not _is_page_closed(existing):
            try:
                await existing.close()
            except Exception:
                pass
        await self._close_previous_lane_pages(lane, skip=existing)

        page = await self._context.new_page()
        await _navigate_lane_page(page, lane, get_settings())
        self._lane_pages[lane.key] = page
        self._lane_contexts[lane.key] = lane
        self._page = page
        return page

    async def _close_previous_lane_pages(self, lane: LaneContext, *, skip: Any | None = None) -> None:
        conv_id = _conversation_id(getattr(lane, "previous_target_url", None))
        if conv_id is None or self._context is None:
            return
        for page in list(self._context.pages):
            if page is skip or _is_page_closed(page):
                continue
            try:
                if _conversation_id(page.url) == conv_id:
                    await page.close()
            except Exception:
                continue

    async def _adopt_matching_page(self, lane: LaneContext) -> Any | None:
        """Reuse an already-open tab for this lane's conversation (and close any
        duplicates of it). Prevents tab pile-up after an api restart clears the
        in-memory lane->page map. Only de-dupes concrete conversations (/c/<id>),
        not the project home."""
        conv_id = _conversation_id(lane.target_url)
        if conv_id is None or self._context is None:
            return None
        matches = []
        for page in self._context.pages:
            try:
                if not _is_page_closed(page) and _conversation_id(page.url) == conv_id:
                    matches.append(page)
            except Exception:
                continue
        if not matches:
            return None
        keep = matches[0]
        for extra in matches[1:]:
            try:
                await extra.close()
            except Exception:
                pass
        return keep

    async def start(self) -> None:
        if self.started:
            return
        settings = get_settings()
        settings.ensure_dirs()
        from patchright.async_api import async_playwright

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
            self._lane_pages = {}
            self._lane_contexts = {}

    async def detach(self) -> None:
        await self.stop()

    async def status(self) -> dict[str, Any]:
        settings = get_settings()
        chrome_info = await _cdp_endpoint_info(settings) if is_cdp_mode(settings.browser_mode) else {}
        chrome_running = self.started or bool(chrome_info)
        chrome_version = chrome_info.get("Browser")
        cdp_attached = self.started
        page = self._page
        lane_status = {
            key: {
                "wechat_account": lane.wechat_account,
                "chat_type": lane.chat_type,
                "peer_id": lane.peer_id,
                "project": lane.project,
                "target_url": lane.target_url,
                "page_closed": _is_page_closed(self._lane_pages.get(key)),
            }
            for key, lane in sorted(self._lane_contexts.items())
        }
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
                "lanes": lane_status,
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
            "lanes": lane_status,
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


async def _navigate_lane_page(page: Any, lane: LaneContext, settings: Settings) -> None:
    target_url = lane.target_url or settings.chatgpt_url
    if not target_url:
        return
    try:
        current_url = page.url
    except Exception:
        current_url = None
    if current_url == target_url:
        return
    if should_navigate_to_chatgpt(current_url) or lane.target_url:
        await page.goto(target_url, wait_until="domcontentloaded")


def _conversation_id(url: str | None) -> str | None:
    if not url or "/c/" not in url:
        return None
    tail = url.split("/c/", 1)[1]
    return tail.split("?")[0].split("#")[0].split("/")[0] or None


def _is_page_closed(page: Any | None) -> bool:
    if page is None:
        return True
    try:
        is_closed = page.is_closed
        return bool(is_closed() if callable(is_closed) else is_closed)
    except Exception:
        return False


def detect_cloudflare_challenge(url: str | None, title: str | None) -> bool:
    haystack = f"{url or ''}\n{title or ''}".lower()
    return any(
        marker in haystack
        for marker in (
            "just a moment",
            "challenge-platform",
            "cloudflare",
            "verify you are human",
            "请稍候",
            "正在验证",
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
