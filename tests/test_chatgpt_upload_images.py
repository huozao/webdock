"""上传附件必须被证实落地。

2026-08-15/16 生产三次实测：`/新对话` 刚导航完的页面吞掉了 set_input_files，
旧代码照样返回成功，于是只发文字，ChatGPT 40 秒后回"没有收到原图"。
这些用例锁住新契约：没长出附件才叫失败，失败要重试，仍不落地就返回 0。
"""

import asyncio

import pytest

from src.browser import chatgpt_page


class FakeLocator:
    def __init__(self, counter, *, on_click=None):
        self._counter = counter
        self._on_click = on_click

    async def count(self):
        return self._counter()

    @property
    def first(self):
        return self

    async def click(self, timeout=None):
        if self._on_click is None:
            raise AssertionError("clicked a locator that is not clickable")
        self._on_click()


def _is_file_input(selector: str) -> bool:
    return "input[type='file']" in selector


class FakePage:
    """最小 composer 模型：set_input_files 是否真的加上附件由 attach_on 控制。"""

    def __init__(
        self,
        *,
        attach_on: int | None = 1,
        chips_before: int = 0,
        has_input: bool = True,
        composer_ready: bool = True,
    ):
        self.attach_on = attach_on
        self.chips = chips_before
        self.has_input = has_input
        self.composer_ready = composer_ready
        self.sets: list[list[str]] = []
        self.events: list[str] = []
        self.url = "https://chatgpt.com/g/g-p-x/project"

    async def wait_for_selector(self, selector, state="attached", timeout=1000):
        if _is_file_input(selector) and self.has_input:
            return object()
        if selector in chatgpt_page.selectors.CHAT_INPUT and self.composer_ready:
            return object()
        raise TimeoutError(selector)

    def locator(self, selector):
        # 只有第一个 ATTACHMENT_PREVIEW 选择器计数，避免重叠选择器把数量翻倍。
        if selector == chatgpt_page.selectors.ATTACHMENT_PREVIEW[0]:
            return FakeLocator(lambda: self.chips)
        if _is_file_input(selector):
            return FakeLocator(lambda: 1 if self.has_input else 0)
        if selector in chatgpt_page.selectors.CHAT_INPUT:
            return FakeLocator(lambda: 1, on_click=lambda: self.events.append("focus"))
        return FakeLocator(lambda: 0)

    async def set_input_files(self, selector, paths):
        self.events.append("set")
        self.sets.append(list(paths))
        if self.attach_on is not None and len(self.sets) >= self.attach_on:
            self.chips += len(paths)


IMAGES = ["data:image/png;base64,iVBORw0KGgo="]


@pytest.fixture(autouse=True)
def fast_upload_waits(monkeypatch):
    monkeypatch.setattr(chatgpt_page, "_UPLOAD_DETECT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(chatgpt_page, "_UPLOAD_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(chatgpt_page, "_COMPOSER_READY_TIMEOUT_SECONDS", 0.3)


def test_upload_returns_count_when_attachment_lands():
    page = FakePage(attach_on=1)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 1
    assert len(page.sets) == 1


def test_upload_retries_once_when_first_set_is_swallowed():
    """第一次 set 被吞（新页竞态），第二次落地 -> 算成功，且不再多设一次。"""
    page = FakePage(attach_on=2)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 1
    assert len(page.sets) == 2


def test_composer_is_woken_before_the_first_set():
    """先激活编辑器再放文件。2026-08-17 实测：project 页导航后 1.4s 连
    input[type=file] 都不存在，2.8s 才和编辑器一起出现——旧代码一见到 input 就
    set，于是 8 条 project 记录全部第一次打空。"""
    page = FakePage(attach_on=1)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 1
    assert page.events[0] == "focus"
    assert page.events.index("focus") < page.events.index("set")


def test_upload_still_tries_when_composer_never_settles():
    """就绪等待是尽力而为：编辑器一直不可见也要照常尝试，成败仍由附件计数说了算。"""
    page = FakePage(attach_on=1, composer_ready=False)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 1
    assert "focus" not in page.events


def test_upload_has_a_spare_attempt_beyond_the_project_page_pattern():
    """project 页第一次必打空、第二次通常成——两次就等于零余量（2026-08-17 那次
    第二次也没赶上就直接失败了）。第三次是余量，不是可有可无的重试。"""
    assert chatgpt_page._UPLOAD_ATTEMPTS >= 3
    page = FakePage(attach_on=3)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 1
    assert len(page.sets) == 3


def test_upload_returns_zero_when_nothing_ever_lands():
    page = FakePage(attach_on=None)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 0
    assert len(page.sets) == chatgpt_page._UPLOAD_ATTEMPTS


def test_upload_returns_zero_when_file_input_missing():
    page = FakePage(has_input=False)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 0
    assert page.sets == []


def test_existing_thread_images_do_not_count_as_this_upload():
    """会话里本来就有图（chips_before>0）时，只有"变多"才算落地。"""
    page = FakePage(attach_on=None, chips_before=3)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 0


def test_slow_but_landed_upload_is_not_set_twice():
    """第一次其实成功、只是超出检测窗口 -> 第二轮看到 baseline>0，不再重复 set。"""
    page = FakePage(attach_on=1)

    async def scenario():
        original = chatgpt_page._wait_uploads_ready
        calls = {"n": 0}

        async def slow_detect(page_arg, has_documents=False, baseline=0):
            calls["n"] += 1
            if calls["n"] == 1:
                return False  # 附件已加上，但这一轮没看见
            return await original(page_arg, has_documents=has_documents, baseline=baseline)

        chatgpt_page._wait_uploads_ready = slow_detect
        try:
            return await chatgpt_page.upload_images(page, IMAGES)
        finally:
            chatgpt_page._wait_uploads_ready = original

    assert asyncio.run(scenario()) == 1
    assert len(page.sets) == 1
