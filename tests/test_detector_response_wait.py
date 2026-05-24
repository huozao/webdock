from __future__ import annotations

import asyncio

from src.browser.detector import assistant_message_count, wait_for_response_complete


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self.page = page
        self.selector = selector
        self.index = -1

    async def count(self) -> int:
        if self.selector == "article:has([data-message-author-role='assistant'])":
            return 0
        if "assistant" in self.selector:
            return len(self.page.assistant_texts)
        return 0

    def nth(self, index: int) -> "FakeLocator":
        item = FakeLocator(self.page, self.selector)
        item.index = index
        return item

    async def inner_text(self, timeout: int = 1500) -> str:
        return self.page.assistant_texts[self.index]


class FakePage:
    def __init__(self, assistant_texts: list[str]) -> None:
        self.assistant_texts = assistant_texts

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


def test_wait_for_response_does_not_return_existing_assistant_message():
    page = FakePage(["old answer"])

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=1, stable_seconds=0, previous_count=1))

    assert answer == ""


def test_wait_for_response_returns_new_assistant_message():
    page = FakePage(["old answer", "new answer"])

    assert asyncio.run(assistant_message_count(page)) == 2
    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=1, stable_seconds=0, previous_count=1))

    assert answer == "new answer"
