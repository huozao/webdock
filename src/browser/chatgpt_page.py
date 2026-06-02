from __future__ import annotations

import asyncio
import time
from typing import Any

from src.browser import selectors
from src.browser.debug_dump import save_debug_dump
from src.browser.detector import assistant_message_count, any_selector_found, find_first, wait_for_response_complete
from src.browser.human import hover_and_click, paste_text, random_delay
from src.config import get_settings
from src.utils.errors import ErrorCode, RelayError

# ChatGPT renders rich widgets (clock / weather / stock cards) in a container
# whose class contains "WidgetRenderer" (and is marked not-markdown). Their text
# is noise, so we screenshot them and send as images instead.
WIDGET_SELECTOR = "[class*='WidgetRenderer']"
MAX_WIDGETS_PER_REPLY = 4


class ChatGPTPage:
    def __init__(self, page: Any, media_store: Any | None = None) -> None:
        self.page = page
        self._media_store = media_store

    async def ask(self, message: str) -> tuple[str, float]:
        settings = get_settings()
        started = time.monotonic()
        try:
            if await any_selector_found(self.page, selectors.LOGIN_INDICATORS):
                raise RelayError(
                    ErrorCode.NOT_LOGGED_IN,
                    "ChatGPT is not logged in. Open noVNC and sign in manually.",
                )

            input_selector = await find_first(self.page, selectors.CHAT_INPUT, visible=True, timeout_ms=2500)
            if not input_selector:
                raise RelayError(
                    ErrorCode.CHAT_INPUT_NOT_FOUND,
                    "Cannot find ChatGPT input box. Open noVNC to inspect the page.",
                )
            previous_assistant_count = await assistant_message_count(self.page)

            await random_delay(settings.before_type_delay_min_ms, settings.before_type_delay_max_ms)
            await paste_text(
                self.page,
                input_selector,
                message,
                delay_min_ms=settings.typing_delay_min_ms,
                delay_max_ms=settings.typing_delay_max_ms,
            )

            send_selector = await find_first(self.page, selectors.SEND_BUTTON, visible=True, timeout_ms=2500)
            if not send_selector:
                raise RelayError(
                    ErrorCode.SEND_BUTTON_NOT_FOUND,
                    "Cannot find ChatGPT send button. Open noVNC to inspect the page.",
                )
            await random_delay(settings.before_send_delay_min_ms, settings.before_send_delay_max_ms)
            await hover_and_click(self.page, send_selector)

            answer = await wait_for_response_complete(
                self.page,
                timeout_seconds=settings.chat_timeout_seconds,
                stable_seconds=settings.response_stable_seconds,
                previous_count=previous_assistant_count,
            )
            if not answer:
                raise RelayError(
                    ErrorCode.RESPONSE_TIMEOUT,
                    "ChatGPT response did not finish before timeout.",
                )

            if not answer.strip():
                raise RelayError(ErrorCode.RESPONSE_EMPTY, "ChatGPT response is empty.")

            final_answer = answer.strip()
            final_answer = await self._append_widget_images(final_answer, settings.media_base_url)
            if settings.test_media_url:
                # Manual link-check switch (browser_data/runtime.json).
                final_answer = f"{final_answer}\nMEDIA: {settings.test_media_url}".strip()
            return final_answer, round(time.monotonic() - started, 3)
        except RelayError as exc:
            exc.debug_dir = await save_debug_dump(self.page, exc)
            raise
        except Exception as exc:
            debug_dir = await save_debug_dump(self.page, exc)
            raise RelayError(ErrorCode.UNKNOWN_ERROR, str(exc), debug_dir=debug_dir) from exc

    async def _append_widget_images(self, answer: str, media_base_url: str) -> str:
        """Screenshot any ChatGPT widgets in the latest reply, store them, and
        append 'MEDIA: <url>' tokens so OpenClaw forwards them to WeChat as
        images. No-op without a media store / base url (so it stays off until
        configured)."""
        base = (media_base_url or "").rstrip("/")
        if self._media_store is None or not base:
            return answer
        result = answer
        for token in await self._capture_widget_tokens():
            result = f"{result}\nMEDIA: {base}/media/{token}".strip()
        return result

    async def _capture_widget_tokens(self) -> list[str]:
        tokens: list[str] = []
        try:
            assistant = self.page.locator(selectors.ASSISTANT_MESSAGE[-1]).last
            widgets = assistant.locator(WIDGET_SELECTOR)
            count = await widgets.count()
        except Exception:
            return tokens
        for index in range(min(count, MAX_WIDGETS_PER_REPLY)):
            widget = widgets.nth(index)
            await _wait_widget_rendered(widget)
            try:
                png = await widget.screenshot(timeout=5000)
            except Exception:
                continue
            try:
                tokens.append(self._media_store.put(png, "image/png"))
            except Exception:
                continue
        return tokens


async def _wait_widget_rendered(widget: Any, timeout_seconds: float = 8.0) -> None:
    """Wait until a widget actually renders before screenshotting (otherwise we
    capture a blank container). Polls inner_text until it's non-empty and stable."""
    last: str | None = None
    stable = 0
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            text = (await widget.inner_text(timeout=2000)).strip()
        except Exception:
            text = ""
        if text and text == last:
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
            last = text
        await asyncio.sleep(0.5)
