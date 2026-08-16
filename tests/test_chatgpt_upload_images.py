"""上传附件必须被证实落地。

2026-08-15/16 生产三次实测：`/新对话` 刚导航完的页面吞掉了 set_input_files，
旧代码照样返回成功，于是只发文字，ChatGPT 40 秒后回"没有收到原图"。
这些用例锁住新契约：没长出附件才叫失败，失败要重试，仍不落地就返回 0。
"""

import asyncio

import pytest

from src.browser import chatgpt_page


class FakeLocator:
    def __init__(self, counter):
        self._counter = counter

    async def count(self):
        return self._counter()


class FakePage:
    """最小 composer 模型：set_input_files 是否真的加上附件由 attach_on 控制。"""

    def __init__(self, *, attach_on: int | None = 1, chips_before: int = 0, has_input: bool = True):
        self.attach_on = attach_on
        self.chips = chips_before
        self.has_input = has_input
        self.sets: list[list[str]] = []
        self.url = "https://chatgpt.com/g/g-p-x/project"

    async def wait_for_selector(self, selector, state="attached", timeout=1000):
        if selector == "input[type='file']" and self.has_input:
            return object()
        raise TimeoutError(selector)

    def locator(self, selector):
        # 只有第一个 ATTACHMENT_PREVIEW 选择器计数，避免重叠选择器把数量翻倍。
        if selector == chatgpt_page.selectors.ATTACHMENT_PREVIEW[0]:
            return FakeLocator(lambda: self.chips)
        return FakeLocator(lambda: 0)

    async def set_input_files(self, selector, paths):
        self.sets.append(list(paths))
        if self.attach_on is not None and len(self.sets) >= self.attach_on:
            self.chips += len(paths)


IMAGES = ["data:image/png;base64,iVBORw0KGgo="]


@pytest.fixture(autouse=True)
def fast_upload_waits(monkeypatch):
    monkeypatch.setattr(chatgpt_page, "_UPLOAD_DETECT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(chatgpt_page, "_UPLOAD_SETTLE_SECONDS", 0.0)


def test_upload_returns_count_when_attachment_lands():
    page = FakePage(attach_on=1)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 1
    assert len(page.sets) == 1


def test_upload_retries_once_when_first_set_is_swallowed():
    """第一次 set 被吞（新页竞态），第二次落地 -> 算成功，且只重试一次。"""
    page = FakePage(attach_on=2)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 1
    assert len(page.sets) == 2


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
