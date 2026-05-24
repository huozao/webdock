from __future__ import annotations

import asyncio
import time
from typing import Any

from src.browser import selectors
from src.browser.human import idle_mouse_movement


async def find_first(page: Any, selector_list: list[str], *, visible: bool = False, timeout_ms: int = 1000) -> str | None:
    state = "visible" if visible else "attached"
    for selector in selector_list:
        try:
            await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
            return selector
        except Exception:
            continue
    return None


async def any_selector_found(page: Any, selector_list: list[str]) -> bool:
    for selector in selector_list:
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


async def latest_assistant_text(page: Any) -> str:
    for selector in selectors.ASSISTANT_MESSAGE:
        try:
            items = page.locator(selector)
            count = await items.count()
            if count > 0:
                text = await items.nth(count - 1).inner_text(timeout=1500)
                return _clean_assistant_text(text)
        except Exception:
            continue
    return ""


async def assistant_message_count(page: Any) -> int:
    for selector in selectors.ASSISTANT_MESSAGE:
        try:
            count = await page.locator(selector).count()
            if count > 0:
                return count
        except Exception:
            continue
    return 0


async def wait_for_response_complete(
    page: Any,
    *,
    timeout_seconds: int,
    stable_seconds: int,
    previous_count: int = 0,
) -> str:
    start = time.monotonic()
    last_text = ""
    stable_for = 0

    while time.monotonic() - start < timeout_seconds:
        current_count = await assistant_message_count(page)
        current = await latest_assistant_text(page)
        stop_visible = await any_selector_found(page, selectors.STOP_BUTTON)
        has_new_assistant = current_count > previous_count

        if current and current == last_text:
            stable_for += 1
        else:
            stable_for = 0
            last_text = current

        if has_new_assistant and current and stable_for >= stable_seconds and not stop_visible:
            return current

        if int(time.monotonic() - start) > 0 and int(time.monotonic() - start) % 10 == 0:
            await idle_mouse_movement(page)

        await asyncio.sleep(1)

    return ""


def _clean_assistant_text(text: str) -> str:
    cleaned = (text or "").strip()
    prefixes = ("ChatGPT said:", "ChatGPT 说：", "ChatGPT said")
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned
