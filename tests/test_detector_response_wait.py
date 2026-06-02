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
        if s == "article:has([data-message-author-role='assistant'])":
            return 0
        if "assistant" in s:
            return len(self.page.assistant_texts)
        return 0

    def nth(self, index: int) -> "FakeLocator":
        item = FakeLocator(self.page, self.selector)
        item.index = index
        return item

    async def inner_text(self, timeout: int = 1500) -> str:
        return self.page.assistant_texts[self.index]


class FakePage:
    def __init__(self, assistant_texts: list[str], streaming: bool = False) -> None:
        self.assistant_texts = assistant_texts
        self.streaming = streaming

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def evaluate(self, script: str):
        # No JS engine in tests -> force rich_assistant_text to fall back to inner_text.
        raise RuntimeError("no evaluate in fake page")


def test_wait_for_response_does_not_return_existing_assistant_message():
    page = FakePage(["old answer"])

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=1, stable_seconds=0, previous_count=1))

    assert answer == ""


def test_wait_for_response_returns_new_assistant_message():
    page = FakePage(["old answer", "new answer"])

    assert asyncio.run(assistant_message_count(page)) == 2
    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=1, stable_seconds=0, previous_count=1))

    assert answer == "new answer"


def test_wait_does_not_return_while_streaming():
    # streaming=True -> "generating", and timeout is too short to hit the stuck-grace,
    # so it must NOT return (would have returned prematurely under the old logic).
    page = FakePage(["partial"], streaming=True)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=3, stable_seconds=1, previous_count=0))

    assert answer == ""


def test_wait_returns_when_streaming_signal_is_stuck(monkeypatch):
    # Generation finished but the streaming/stop signal is wrongly stuck on.
    # After text is stable past stable_seconds + grace, it should return anyway
    # (this is the timeout bug fix).
    monkeypatch.setattr(detector, "STUCK_GRACE_SECONDS", 1)
    page = FakePage(["final answer"], streaming=True)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=10, stable_seconds=1, previous_count=0))

    assert answer == "final answer"
