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


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_wait_extends_past_soft_timeout_while_progress_changes(monkeypatch):
    clock = FakeClock()
    page = FakePage([""])

    async def changing_text(_page):
        if clock.now < 2:
            return "正在处理第 1 步"
        if clock.now < 4:
            return "正在处理第 2 步"
        return "最终结果"

    monkeypatch.setattr(detector.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(detector.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(detector, "rich_assistant_text", changing_text)

    answer = asyncio.run(
        wait_for_response_complete(
            page,
            timeout_seconds=2,
            stable_seconds=0,
            idle_timeout_seconds=2,
            hard_timeout_seconds=8,
        )
    )

    assert answer == "最终结果"
    assert clock.now == 4


def test_wait_times_out_after_post_soft_deadline_inactivity(monkeypatch):
    clock = FakeClock()
    page = FakePage(["正在处理"], stop_button=True)
    monkeypatch.setattr(detector.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(detector.asyncio, "sleep", clock.sleep)

    answer = asyncio.run(
        wait_for_response_complete(
            page,
            timeout_seconds=2,
            stable_seconds=0,
            idle_timeout_seconds=2,
            hard_timeout_seconds=8,
        )
    )

    assert answer is None
    assert clock.now == 4


def test_wait_hard_timeout_wins_even_with_continuous_progress(monkeypatch):
    clock = FakeClock()
    page = FakePage([""])

    async def always_changing(_page):
        return f"正在处理第 {int(clock.now)} 步"

    monkeypatch.setattr(detector.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(detector.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(detector, "rich_assistant_text", always_changing)

    answer = asyncio.run(
        wait_for_response_complete(
            page,
            timeout_seconds=1,
            stable_seconds=0,
            idle_timeout_seconds=2,
            hard_timeout_seconds=5,
        )
    )

    assert answer is None
    assert clock.now == 5


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


def test_wait_returns_when_residual_streaming_lingers(monkeypatch):
    # The stop button is GONE but a residual .result-streaming class lingers (a
    # stale "generating" signal). The stop button is the authoritative completion
    # signal, so once the text is stable past stable_seconds + grace, return anyway
    # instead of hanging until timeout.
    monkeypatch.setattr(detector, "STUCK_GRACE_SECONDS", 1)
    page = FakePage(["final answer"], streaming=True, stop_button=False)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=10, stable_seconds=1, previous_count=0))

    assert answer == "final answer"


def test_wait_keeps_waiting_while_stop_button_present(monkeypatch):
    # The stop button is the authoritative "still generating" signal: while it is
    # present (a preamble→已思考→answer reply, or ChatGPT generating a DALL-E image)
    # the reply must NOT complete even when the current text is stable past grace —
    # returning now would grab the preamble / previous reply (the 62-char truncation
    # bug). Times out here since the stop button never clears.
    monkeypatch.setattr(detector, "STUCK_GRACE_SECONDS", 1)
    page = FakePage(["partial"], streaming=False, stop_button=True)

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


def test_wait_holds_while_image_generating(monkeypatch):
    # While the image-gen loading placeholder is up (image_generating True) and no
    # NEW image yet, keep waiting — ignore interim text/reasoning.
    page = FakePage(["正在生成图片，请稍候"], streaming=False)

    async def gen(_page):
        return True

    monkeypatch.setattr(detector, "image_generating", gen)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=2, stable_seconds=0, previous_count=0))

    assert answer is None


def test_wait_does_not_return_stale_reply_during_generation():
    # Sending into a conversation that already showed a widget+text reply. While
    # ChatGPT generates the new answer the page still shows that OLD reply (same
    # text, same widget, no new image). Must NOT return the stale reply — this is
    # the "画熊猫 got the previous clock back" bug (was caused by observed_generating).
    page = FakePage(["现在是 23:55"], streaming=True, widget_count=1)

    answer = asyncio.run(
        wait_for_response_complete(
            page,
            timeout_seconds=3,
            stable_seconds=0,
            previous_count=1,
            previous_text="现在是 23:55",
            previous_has_widget=True,
        )
    )

    assert answer is None


def test_wait_returns_when_new_image_src_appears(monkeypatch):
    # Repeated image request: an image was already on the page before sending.
    # A NEW image (different src) appearing must count as a new reply — otherwise
    # we'd resend the earlier image (the "连续画图发第一张" bug).
    page = FakePage([""], streaming=False)

    async def fake_srcs(_page, min_px=200):
        return ["url_OLD", "url_NEW"]

    monkeypatch.setattr(detector, "generated_image_srcs", fake_srcs)

    answer = asyncio.run(
        wait_for_response_complete(
            page,
            timeout_seconds=2,
            stable_seconds=0,
            previous_count=1,
            previous_text="",
            previous_image_srcs=["url_OLD"],
        )
    )

    assert answer == ""  # completes because a new image src appeared


def test_generated_image_srcs_excludes_user_turn_images():
    class InspectPage:
        script = ""

        async def evaluate(self, script, min_px=200):
            self.script = script
            return []

    page = InspectPage()

    asyncio.run(detector.generated_image_srcs(page))

    assert "[data-testid^='conversation-turn']" in page.script
    assert "[data-message-author-role='user']" in page.script


def test_wait_holds_while_generating_ignores_reasoning(monkeypatch):
    # Placeholder up + interim English reasoning that matches NO keyword: must keep
    # waiting (the "画X got reasoning text" bug). Uses the DOM placeholder signal,
    # not text keywords.
    page = FakePage(["Generating another cartoon pig. The user asks for..."], streaming=False)

    async def gen(_page):
        return True

    monkeypatch.setattr(detector, "image_generating", gen)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=2, stable_seconds=0, previous_count=0))

    assert answer is None


def test_wait_returns_text_when_not_generating_image():
    # No image-gen placeholder (e.g. ChatGPT refused the image) -> return the text
    # reply instead of hanging forever on an image that never comes.
    page = FakePage(["old", "抱歉，无法生成该图片"], streaming=False)

    answer = asyncio.run(
        wait_for_response_complete(page, timeout_seconds=2, stable_seconds=0, previous_count=1, previous_text="old")
    )

    assert answer == "抱歉，无法生成该图片"


def test_wait_holds_for_interim_thinking_text():
    # Long reasoning BEFORE the image-gen placeholder appears: only interim
    # "正在思考/正在生成" status text shows. Must keep waiting (the 55s-thinking bug).
    page = FakePage(["正在思考\n正在生成更细致的图片，请稍候。"], streaming=False)

    answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=2, stable_seconds=0, previous_count=0))

    assert answer is None


def test_wait_holds_for_various_interim_status():
    # Any tool/working status (search, analyze, run code, read... not just image)
    # keeps the wait open until it clears.
    for status in ["正在搜索网页", "正在分析文件", "正在运行代码", "正在读取网页", "Searching the web", "Analyzing data"]:
        page = FakePage([status], streaming=False)
        answer = asyncio.run(wait_for_response_complete(page, timeout_seconds=1, stable_seconds=0, previous_count=0))
        assert answer is None, status
