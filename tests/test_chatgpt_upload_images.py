"""上传附件必须被证实**传完**，不只是被浏览器收下。

2026-08-15/16 生产三次实测：`/新对话` 刚导航完的页面吞掉了 set_input_files，
旧代码照样返回成功，于是只发文字，ChatGPT 40 秒后回"没有收到原图"。
这些用例锁住第一层契约：没长出附件才叫失败，失败要重试，仍不落地就返回 0。

2026-08-24 实测推翻了"长出附件=可以发送"这半句：缩略图（连同它的删除按钮）在
上传进度 1% 时就已经在 DOM 里，而真正传完要几分钟。旧判据 attachment_count()
只数"变多没变多"，于是三次采样全部在 1% 时判成功：

  09:08:44 三张图 chip 出现，进度环 1%
  09:08:48 upload_images 返回成功 → 粘文字 → 点发送（进度仍 1%）
  09:10:49 其中一张 tile 消失，ChatGPT **取消**了排队的提交
  09:11:17 剩下两张传完，输入框里留着文字和图，turns=0，永远不会自己发出去

所以第二层契约：每个我们写进去的文件都要按文件名找到自己那个 tile，且都拿到
服务端 file id，才算落地；少一个就是失败，不许发送。
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
    """最小 composer 模型：附件按文件名单独建模，各自有上传进度。

    `attach_on`   第几次 set_input_files 才真的加上 tile（模拟新页竞态吞掉的那几次）
    `land_after`  tile 出现后再被查询几次才拿到 file id（模拟真实上传耗时），
                  None = 永远传不完
    `drop_on_poll`第几次查询时让某个 tile 消失（模拟 08-24 那张丢掉的图）；
                  必须晚于它第一次被看见，否则那就是"从没落地"，另一回事
    """

    def __init__(
        self,
        *,
        attach_on: int | None = 1,
        chips_before: int = 0,
        has_input: bool = True,
        composer_ready: bool = True,
        land_after: int | None = 0,
        drop_on_poll: int | None = None,
    ):
        self.attach_on = attach_on
        self.chips = chips_before
        self.has_input = has_input
        self.composer_ready = composer_ready
        self.land_after = land_after
        self.drop_on_poll = drop_on_poll
        self.sets: list[list[str]] = []
        self.events: list[str] = []
        self.polls = 0
        self.tiles: dict[str, int] = {}  # 文件名 -> 出现时的查询序号
        self.dropped: set[str] = set()
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
            return FakeLocator(lambda: self.chips + len(self.tiles) - len(self.dropped))
        if _is_file_input(selector):
            return FakeLocator(lambda: 1 if self.has_input else 0)
        if selector in chatgpt_page.selectors.CHAT_INPUT:
            return FakeLocator(lambda: 1, on_click=lambda: self.events.append("focus"))
        return FakeLocator(lambda: 0)

    async def set_input_files(self, selector, paths):
        self.events.append("set")
        self.sets.append(list(paths))
        if self.attach_on is not None and len(self.sets) >= self.attach_on:
            for path in paths:
                name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                self.tiles.setdefault(name, self.polls)

    async def evaluate(self, expression, arg=None):
        if expression is not chatgpt_page._ATTACHMENT_STATES_JS:
            raise AssertionError("unexpected evaluate: %r" % expression[:40])
        self.polls += 1
        if self.drop_on_poll is not None and self.polls >= self.drop_on_poll and self.tiles:
            self.dropped.add(sorted(self.tiles)[0])
        states = []
        for name in arg:
            if name not in self.tiles or name in self.dropped:
                states.append("missing")
            elif self.land_after is None or self.polls - self.tiles[name] < self.land_after:
                states.append("uploading")
            else:
                states.append("done")
        return states


IMAGES = ["data:image/png;base64,iVBORw0KGgo="]
THREE_IMAGES = IMAGES * 3


@pytest.fixture(autouse=True)
def fast_upload_waits(monkeypatch):
    monkeypatch.setattr(chatgpt_page, "_UPLOAD_DETECT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(chatgpt_page, "_upload_land_budget", lambda: 1.0)
    monkeypatch.setattr(chatgpt_page, "_UPLOAD_POLL_SECONDS", 0.0)
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
    """就绪等待是尽力而为：编辑器一直不可见也要照常尝试，成败仍由附件说了算。"""
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
    """会话里本来就有图时，它们不叫我们这次的文件名，不能顶替。"""
    page = FakePage(attach_on=None, chips_before=3)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 0


def test_slow_but_landed_upload_is_not_set_twice():
    """第一次其实成功、只是超出检测窗口 -> 第二轮看到 tile 已在，不再重复 set。"""
    page = FakePage(attach_on=1)

    async def scenario():
        original = chatgpt_page._wait_uploads_ready
        calls = {"n": 0}

        async def slow_detect(page_arg, names, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return "not_detected"  # 附件已加上，但这一轮没看见
            return await original(page_arg, names, **kwargs)

        chatgpt_page._wait_uploads_ready = slow_detect
        try:
            return await chatgpt_page.upload_images(page, IMAGES)
        finally:
            chatgpt_page._wait_uploads_ready = original

    assert asyncio.run(scenario()) == 1
    assert len(page.sets) == 1


def test_upload_waits_for_the_file_id_not_just_the_thumbnail():
    """缩略图 1% 就在了：进度未走完前不许返回成功（2026-08-24 根因）。"""
    page = FakePage(attach_on=1, land_after=3)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 1
    assert page.polls >= 3


def test_upload_fails_when_progress_never_finishes():
    """传不完就是失败，不能当成功发出去——发出去也是一轮缺图的对话。"""
    page = FakePage(attach_on=1, land_after=None)
    assert asyncio.run(chatgpt_page.upload_images(page, IMAGES)) == 0


def test_upload_fails_when_one_attachment_disappears():
    """08-24 实测：3 张图有 1 张 tile 中途消失，ChatGPT 同时取消了排队的提交。
    旧判据只数总数、连 3→2 都发现不了；现在少一个就必须失败。"""
    page = FakePage(attach_on=1, land_after=2, drop_on_poll=3)
    assert asyncio.run(chatgpt_page.upload_images(page, THREE_IMAGES)) == 0


def test_vanished_attachment_is_not_retried():
    """附件已经进过 composer 又消失，再 set 一次只会叠出重复附件——直接失败。"""
    page = FakePage(attach_on=1, land_after=2, drop_on_poll=3)
    assert asyncio.run(chatgpt_page.upload_images(page, THREE_IMAGES)) == 0
    assert len(page.sets) == 1
