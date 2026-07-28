"""ensure_mode 只触碰 FakePage 暴露的这几个表面：wait_for_selector /
locator().first(hover/click/inner_text) / keyboard.press。"""
from __future__ import annotations

import asyncio

from src.browser import selectors
from src.browser.chatgpt_page import ChatGPTPage

BUTTON = selectors.MODE_PICKER_BUTTON[0]


class FakeLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    async def hover(self):
        pass

    async def click(self):
        self._page.clicks.append(self._selector)
        self._page.on_click(self._selector)

    async def inner_text(self, timeout=None):
        return self._page.texts.get(self._selector, "")


class FakeKeyboard:
    def __init__(self, page):
        self._page = page

    async def press(self, key):
        self._page.pressed.append(key)


class FakePage:
    def __init__(self, present, texts, on_click=None):
        self.present = set(present)
        self.texts = dict(texts)
        self.clicks = []
        self.pressed = []
        self.waits = []
        self.keyboard = FakeKeyboard(self)
        self._on_click = on_click or (lambda selector: None)

    def on_click(self, selector):
        self._on_click(selector)

    async def wait_for_selector(self, selector, state="attached", timeout=None):
        self.waits.append(selector)
        if selector in self.present:
            return object()
        raise TimeoutError(selector)

    def locator(self, selector):
        return FakeLocator(self, selector)


def test_ensure_mode_skips_when_already_on_target():
    page = FakePage(present={BUTTON}, texts={BUTTON: "高级"})
    asyncio.run(ChatGPTPage(page).ensure_mode("advanced"))
    assert page.clicks == []


def test_ensure_mode_clicks_target_menu_item():
    item = f"{selectors.MODE_MENU_ITEM[0]}:has-text('极速')"

    def on_click(selector):
        if selector == item:
            page.texts[BUTTON] = "极速"

    page = FakePage(present={BUTTON, item}, texts={BUTTON: "高级"}, on_click=on_click)
    asyncio.run(ChatGPTPage(page).ensure_mode("fast"))
    assert page.clicks == [BUTTON, item]


def test_ensure_mode_clicks_english_balanced_medium_item():
    item = f"{selectors.MODE_MENU_ITEM[0]}:has-text('Medium')"

    def on_click(selector):
        if selector == item:
            page.texts[BUTTON] = "Medium"

    page = FakePage(present={BUTTON, item}, texts={BUTTON: "Instant"}, on_click=on_click)
    asyncio.run(ChatGPTPage(page).ensure_mode("balanced"))
    assert page.clicks == [BUTTON, item]


def test_ensure_mode_clicks_english_advanced_high_item():
    item = f"{selectors.MODE_MENU_ITEM[0]}:has-text('High')"

    def on_click(selector):
        if selector == item:
            page.texts[BUTTON] = "High"

    page = FakePage(present={BUTTON, item}, texts={BUTTON: "Instant"}, on_click=on_click)
    asyncio.run(ChatGPTPage(page).ensure_mode("advanced"))
    assert page.clicks == [BUTTON, item]


def test_ensure_mode_missing_button_is_noop():
    page = FakePage(present=set(), texts={})
    asyncio.run(ChatGPTPage(page).ensure_mode("fast"))
    assert page.clicks == [] and page.pressed == []


def test_ensure_mode_missing_menu_item_escapes_and_continues():
    page = FakePage(present={BUTTON}, texts={BUTTON: "高级"})
    asyncio.run(ChatGPTPage(page).ensure_mode("fast"))
    assert page.clicks == [BUTTON]
    assert "Escape" in page.pressed


def test_ensure_mode_none_target_is_noop():
    page = FakePage(present={BUTTON}, texts={BUTTON: "高级"})
    asyncio.run(ChatGPTPage(page).ensure_mode(""))
    assert page.clicks == []


ANY_BUTTON = selectors.MODE_PICKER_BUTTON_ANY


def test_ensure_mode_asks_the_pill_once_when_it_answers():
    # 每个带文案的候选各等满一轮超时，2026-07-28 实测稳定烧掉 6s；胶囊本体能直接
    # 回答"现在是什么模式"时，一次探测就该结束。
    page = FakePage(present={ANY_BUTTON}, texts={ANY_BUTTON: "高级"})

    asyncio.run(ChatGPTPage(page).ensure_mode("advanced"))

    assert page.waits == [ANY_BUTTON]
    assert page.clicks == []


def test_ensure_mode_still_switches_through_the_bare_pill():
    item = f"{selectors.MODE_MENU_ITEM[0]}:has-text('极速')"

    def on_click(selector):
        if selector == item:
            page.texts[ANY_BUTTON] = "极速"

    page = FakePage(present={ANY_BUTTON, item}, texts={ANY_BUTTON: "高级"}, on_click=on_click)

    asyncio.run(ChatGPTPage(page).ensure_mode("fast"))

    assert page.clicks == [ANY_BUTTON, item]


def test_ensure_mode_falls_back_when_the_bare_pill_is_not_the_mode_pill():
    # composer 上还有别的 aria-haspopup 按钮时，文本不像模式文案，必须退回带文案的候选。
    page = FakePage(present={ANY_BUTTON, BUTTON}, texts={ANY_BUTTON: "工具", BUTTON: "极速"})

    asyncio.run(ChatGPTPage(page).ensure_mode("fast"))

    assert page.waits[0] == ANY_BUTTON
    assert BUTTON in page.waits
    assert page.clicks == []
