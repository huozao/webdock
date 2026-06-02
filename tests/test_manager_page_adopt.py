from __future__ import annotations

import asyncio
import dataclasses

from src.browser.manager import BrowserManager, _conversation_id
from src.browser.lane_scheduler import LaneContext


def test_conversation_id():
    assert _conversation_id("https://chatgpt.com/g/g-p-x/c/abc-123") == "abc-123"
    assert _conversation_id("https://chatgpt.com/c/abc-123?foo=1#y") == "abc-123"
    assert _conversation_id("https://chatgpt.com/g/g-p-x/project") is None
    assert _conversation_id("https://chatgpt.com/") is None
    assert _conversation_id(None) is None


class FakePage:
    def __init__(self, url: str, closed: bool = False) -> None:
        self._url = url
        self._closed = closed
        self.closed_called = False

    @property
    def url(self) -> str:
        return self._url

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self.closed_called = True
        self._closed = True


class FakeContext:
    def __init__(self, pages: list) -> None:
        self.pages = pages


def _lane(target_url: str) -> LaneContext:
    lane = LaneContext.from_metadata({"peer_id": "u1"})
    return dataclasses.replace(lane, target_url=target_url)


def test_adopt_reuses_first_and_closes_duplicates():
    conv = "https://chatgpt.com/g/g-p-x/c/dup-1"
    p1 = FakePage(conv)
    p2 = FakePage(conv)
    p3 = FakePage("https://chatgpt.com/c/other")
    mgr = BrowserManager()
    mgr._context = FakeContext([p1, p2, p3])

    adopted = asyncio.run(mgr._adopt_matching_page(_lane(conv)))

    assert adopted is p1            # reuse the first matching tab
    assert p2.closed_called         # duplicate of same conversation closed
    assert not p3.closed_called     # unrelated tab untouched


def test_adopt_returns_none_for_project_home():
    mgr = BrowserManager()
    mgr._context = FakeContext([FakePage("https://chatgpt.com/g/g-p-x/c/a")])

    # project home (no /c/) -> don't adopt, let it open a fresh conversation
    assert asyncio.run(mgr._adopt_matching_page(_lane("https://chatgpt.com/g/g-p-x/project"))) is None


def test_adopt_returns_none_when_no_match():
    mgr = BrowserManager()
    mgr._context = FakeContext([FakePage("https://chatgpt.com/g/g-p-x/c/aaa")])

    assert asyncio.run(mgr._adopt_matching_page(_lane("https://chatgpt.com/g/g-p-x/c/bbb"))) is None
