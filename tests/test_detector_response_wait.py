from __future__ import annotations

import asyncio

import src.browser.detector as detector
from src.browser.detector import assistant_message_count, wait_for_response_complete


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self.page = page
        self.selector = selector
        self.index = -1

    async def count(self) -> int:
        s = self.selector
        if "result-streaming" in s:
            return 1 if self.page.streaming else 0
        if "stop-button" in s:
            return 1 if self.page.stop_button else 0
        if "WidgetRenderer" in s:
            return self.page.widget_count
        if s == "article:has([data-message-author-role='assistant'])":
            return 0
        if "assistant" in s:
            return len(self.page.assistant_texts)
        return 0

    @property
    def last(self) -> "FakeLocator":
        item = FakeLocator(self.page, self.selector)
        item.index = len(self.page.assistant_texts) - 1
        return item

    def nth(self, index: int) -> "FakeLocator":
        item = FakeLocator(self.page, self.selector)
        item.index = index
        return item

    def locator(self, selector: str) -> "FakeLocator":
        return FakeLocator(self.page, selector)

    async def inner_text(self, timeout: int = 1500) -> str:
        return self.page.assistant_texts[self.index]


class FakePage:
    def __init__(
        self,
        assistant_texts: list[str],
        streaming: bool = False,
        widget_count: int = 0,
        stop_button: bool = False,
    ) -> None:
        self.assistant_texts = assistant_texts
        self.streaming = streaming
        self.widget_count = widget_count
        self.stop_button = stop_button

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def evaluate(self, script: str):
        # No JS engine in tests -> force rich_assistant_text to fall back to inner_text.
        raise RuntimeError("no evaluate in fake page")


def test_wait_times_out_when_no_new_assistant_message():
    # Same text as before sending, count didn't grow, no generation observed -> not new.
    page = FakePage(["old answer"])

    answer = asyncio.run(
        wait_for_response_complete(
            page, timeout_seconds=1, stable_seconds=0, previous_count=1, previous_text="old answer"
        )
    )

    assert answer is None


def test_wait_for_response_returns_new_assistant_message():
    page = FakePage(["old answer", "new answer"])

    assert asyncio.run(assistant_message_count(page)) == 2
    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=1, stable_seconds=0, previous_count=1))

    assert answer == "new answer"


def test_wait_does_not_return_while_streaming():
    # streaming=True -> "generating", and timeout is too short to hit the stuck-grace,
    # so it must NOT complete -> times out (None).
    page = FakePage(["partial"], streaming=True)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=3, stable_seconds=1, previous_count=0))

    assert answer is None


def test_wait_returns_when_residual_stop_button_lingers(monkeypatch):
    # result-streaming is GONE but a stop/read-aloud button lingers (a false
    # "generating" signal). After text is stable past stable_seconds + grace,
    # return anyway.
    monkeypatch.setattr(detector, "STUCK_GRACE_SECONDS", 1)
    page = FakePage(["final answer"], streaming=False, stop_button=True)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=10, stable_seconds=1, previous_count=0))

    assert answer == "final answer"


def test_wait_keeps_waiting_while_truly_streaming(monkeypatch):
    # result-streaming genuinely active (e.g. ChatGPT generating a DALL-E image):
    # even with text stable past grace, must NOT return early — that would grab the
    # PREVIOUS reply. Keeps waiting (times out here since streaming never ends).
    monkeypatch.setattr(detector, "STUCK_GRACE_SECONDS", 1)
    page = FakePage(["partial"], streaming=True)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=4, stable_seconds=1, previous_count=0))

    assert answer is None


def test_wait_returns_widget_only_reply_with_empty_text():
    # New assistant message is a widget card with no markdown text: rich_assistant_text
    # yields "" (falls back to inner_text="") but a widget is present -> completion
    # fires and returns "" (NOT None), so the caller can deliver the screenshot.
    page = FakePage([""], streaming=False, widget_count=1)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=3, stable_seconds=0, previous_count=0))

    assert answer == ""


def test_wait_times_out_on_empty_text_without_widget():
    # Empty text AND no widget -> not "ready" -> must time out (None), so an
    # un-arrived/blank reply is never mistaken for a finished one.
    page = FakePage([""], streaming=False, widget_count=0)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=2, stable_seconds=0, previous_count=0))

    assert answer is None


def test_wait_returns_when_count_static_but_text_changed():
    # ChatGPT virtualizes the message list: a NEW reply arrives but the visible
    # assistant node count stays the same (previous_count == current_count). Since
    # the text differs from before sending, it must still complete. This is the
    # real root cause of the widget-reply timeout (has_new used to rely on count).
    page = FakePage(["现在是 21:22"], streaming=False)

    answer = asyncio.run(
        wait_for_response_complete(
            page, timeout_seconds=3, stable_seconds=0, previous_count=1, previous_text="现在是 19:17"
        )
    )

    assert answer == "现在是 21:22"
