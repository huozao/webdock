"""Sidebar entry into a ChatGPT project home page.

Background (2026-09-09): a full navigation to ``/g/<gizmo>/project`` renders
ChatGPT's error boundary — body is literally "Try again", no composer, no file
input — while the document returns HTTP 200 and every backend-api call succeeds.
Reproduced on four projects and on both webdock1 and webdock2, so it is neither
a device nor a network fault. Reaching the same page by clicking its sidebar row
works. These tests pin the parts of that workaround that can silently rot.
"""
from __future__ import annotations

import asyncio

import pytest

from src.api.routes_chat import _describe_start_failure
from src.browser import selectors
from src.browser.manager import is_project_home_url, parse_project_target


PROJECT_URL = "https://chatgpt.com/g/g-p-6a2ffe0bac248191988612d9081dd6b1-lark-hao/project"
SIBLING_URL = "https://chatgpt.com/g/g-p-6a518a16cef08191a8415c7eaf728552-lark-hao2/project"
CONVERSATION_URL = "https://chatgpt.com/g/g-p-6a2ffe0bac248191988612d9081dd6b1-lark-hao/c/6a9bbeca"


def test_project_url_splits_into_gizmo_and_slug():
    assert parse_project_target(PROJECT_URL) == (
        "g-p-6a2ffe0bac248191988612d9081dd6b1",
        "lark-hao",
    )


def test_slug_containing_hyphens_does_not_eat_the_gizmo_id():
    """A lazy quantifier would return ("g-p-6", "a2ffe...-lark-hao") here and the
    turn would then be checked against a gizmo id that never appears in any URL."""
    gizmo, slug = parse_project_target(SIBLING_URL)
    assert gizmo == "g-p-6a518a16cef08191a8415c7eaf728552"
    assert slug == "lark-hao2"


def test_conversation_pages_are_left_on_the_direct_path():
    """Only ``/project`` is broken; conversation pages under the same project load
    fine by direct navigation and must not pay the sidebar detour."""
    assert parse_project_target(CONVERSATION_URL) == (None, None)
    assert is_project_home_url(CONVERSATION_URL) is False
    assert is_project_home_url(PROJECT_URL) is True
    assert is_project_home_url("https://chatgpt.com/") is False


def test_project_entry_mode_defaults_to_sidebar():
    """runtime.json overrides are ``if key in data`` — a missing key falls all the
    way back to the dataclass default without logging, so the default itself has
    to be the working mode, not the broken one."""
    from src.config import Settings

    assert Settings().project_entry_mode == "sidebar"


@pytest.mark.parametrize(
    "text,expected_fragment",
    [
        ("Cannot connect to Chrome CDP at http://127.0.0.1:9222: boom", "CDP attach failed"),
        ("Page.goto: Timeout 30000ms exceeded.", "initial page load failed"),
        ("something else entirely", "Browser start failed"),
    ],
)
def test_start_failure_names_the_step_that_failed(text, expected_fragment):
    """The old message blamed "Chrome not running or CDP attach failed" for every
    exception, including the goto timeout that actually fired while Chrome was up
    and CDP was answering."""
    assert expected_fragment in _describe_start_failure(RuntimeError(text))


SIDEBAR_HTML = """
<ul>
  <li class="list-none">
    <div data-sidebar-item="true" role="button" class="group __menu-item">Lark-hao</div>
    <button data-trailing-button tabindex="0" aria-label="Open project home"></button>
    <button data-trailing-button tabindex="0" type="button" aria-haspopup="menu"
            aria-expanded="false" data-state="closed"
            aria-label="Open project options for Lark-hao"></button>
  </li>
  <li class="list-none">
    <div data-sidebar-item="true" role="button" class="group __menu-item">lark-hao2</div>
    <button data-trailing-button tabindex="0" aria-label="打开项目首页"></button>
    <button data-trailing-button tabindex="0" type="button" aria-haspopup="menu"
            aria-label="打开 lark-hao2 的项目选项"></button>
  </li>
  <button data-sidebar-item="true" type="button">Show more</button>
</ul>
"""


def test_home_button_is_picked_by_structure_not_label_or_position(rich_markdown_page):
    """The row has two trailing buttons; only the options one carries
    aria-haspopup. Position and aria-label both fail as handles: the label is
    localized (en-US on webdock2, zh-CN on webdock1) and position is exactly the
    kind of near-enough judgement this repo has been burned by before."""
    page = rich_markdown_page
    page.set_content(SIDEBAR_HTML)

    row = page.locator("li").first
    selected = row.locator(selectors.SIDEBAR_PROJECT_HOME_BUTTON[0])
    assert selected.count() == 1
    assert selected.first.get_attribute("aria-label") == "Open project home"

    # Same selector on the zh-CN row: still exactly the navigation button.
    zh_row = page.locator("li").nth(1)
    zh_selected = zh_row.locator(selectors.SIDEBAR_PROJECT_HOME_BUTTON[0])
    assert zh_selected.count() == 1
    assert zh_selected.first.get_attribute("aria-label") == "打开项目首页"

    # The label-based handle that would have looked reasonable misses the zh-CN
    # row entirely — proof this test is guarding a real failure, not a tautology.
    assert zh_row.locator("button[aria-label='Open project home']").count() == 0


def test_show_more_is_not_mistaken_for_a_project_row(rich_markdown_page):
    """The expander also carries data-sidebar-item; project rows are div[role=button]
    and it is a <button>, so the row selector must skip it or the row index used
    for clicking would be off by one."""
    page = rich_markdown_page
    page.set_content(SIDEBAR_HTML)

    rows = page.locator(selectors.SIDEBAR_PROJECT_ITEM[0])
    labels = [t.strip() for t in rows.all_text_contents()]
    assert labels == ["Lark-hao", "lark-hao2"]


def test_row_lookup_is_equality_so_a_prefix_does_not_win(rich_markdown_page):
    """``lark-hao`` is a prefix of ``lark-hao2``. A substring match would route the
    turn into the wrong project and return a plausible reply from the wrong
    context instead of failing."""
    page = rich_markdown_page
    page.set_content(SIDEBAR_HTML)

    rows = page.locator(selectors.SIDEBAR_PROJECT_ITEM[0])
    labels = [t.strip().lower() for t in rows.all_text_contents()]

    assert [i for i, t in enumerate(labels) if t == "lark-hao"] == [0]
    assert [i for i, t in enumerate(labels) if t == "lark-hao2"] == [1]
    # the containment version that this replaces:
    assert [i for i, t in enumerate(labels) if "lark-hao" in t] == [0, 1]


# --- the second navigation site: lane_scheduler._route_page ---------------


class _FakeLocator:
    def __init__(self, page):
        self._page = page

    async def count(self):
        return 1

    async def wait_for(self, **_kwargs):
        return None


class _FakePage:
    """Enough of a Playwright page to see which navigation path was taken."""

    def __init__(self, url: str):
        self.url = url
        self.goto_calls: list[str] = []

    async def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self.url = url

    def locator(self, _selector):
        return _FakeLocator(self)

    async def wait_for_selector(self, _selector, **_kwargs):
        return object()

    async def query_selector(self, _selector):
        return object()


def test_route_page_does_not_bare_goto_a_project_url(monkeypatch):
    """_route_page is a SECOND place that navigates to the lane's target.

    It only fires when the tab's URL differs from the target, which after a
    successful sidebar entry can still happen over something as small as a
    trailing slash — and a bare goto there would immediately undo the sidebar
    entry and leave the tab on "Try again".
    """
    from src.browser import lane_scheduler as ls
    from src.browser import manager

    calls: list[str] = []

    async def fake_open(page, target_url, _settings):
        calls.append(target_url)
        page.url = target_url
        return "sidebar"

    monkeypatch.setattr(manager, "open_project_home", fake_open)

    async def fake_find_first(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(ls, "find_first", fake_find_first)

    scheduler = ls.ChatLaneScheduler(max_concurrent_chats=1, ask_func=None)
    page = _FakePage("https://chatgpt.com/")
    asyncio.run(scheduler._route_page(page, PROJECT_URL, force_new=True))

    assert calls == [PROJECT_URL]
    assert page.goto_calls == []


def test_route_page_still_bare_gotos_a_conversation_url(monkeypatch):
    """The detour is only for /project; conversation pages load fine directly and
    must not pay for it."""
    from src.browser import lane_scheduler as ls

    async def fake_find_first(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(ls, "find_first", fake_find_first)

    scheduler = ls.ChatLaneScheduler(max_concurrent_chats=1, ask_func=None)
    page = _FakePage("https://chatgpt.com/")
    asyncio.run(scheduler._route_page(page, CONVERSATION_URL, force_new=True))

    assert page.goto_calls == [CONVERSATION_URL]
