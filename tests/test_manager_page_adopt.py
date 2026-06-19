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
        self.goto_calls: list[str] = []

    @property
    def url(self) -> str:
        return self._url

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self.closed_called = True
        self._closed = True

    async def goto(self, url: str, **kwargs) -> None:
        self.goto_calls.append(url)
        self._url = url

    async def wait_for_selector(self, selector: str, **kwargs):
        return object()


class FakeContext:
    def __init__(self, pages: list) -> None:
        self.pages = pages
        self.created_pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage("about:blank")
        self.pages.append(page)
        self.created_pages.append(page)
        return page


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


def test_reset_lane_page_closes_existing_and_opens_new_tab():
    old_page = FakePage("https://chatgpt.com/g/g-p-x/c/old")
    context = FakeContext([old_page])
    lane = _lane("https://chatgpt.com/g/g-p-x/project")
    mgr = BrowserManager()
    mgr._context = context
    mgr._lane_pages[lane.key] = old_page
    mgr._lane_contexts[lane.key] = lane

    new_page = asyncio.run(mgr.reset_lane_page(lane))

    assert old_page.closed_called
    assert new_page is context.created_pages[0]
    assert new_page.goto_calls == ["https://chatgpt.com/g/g-p-x/project"]
    assert mgr._lane_pages[lane.key] is new_page
    assert mgr._page is new_page
