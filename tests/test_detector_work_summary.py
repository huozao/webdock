"""ChatGPT 自报耗时必须能读出来。

2026-08-17：用户等了 214s，ChatGPT 页面上写着 "Worked for 10s"。两个数都拿到，
才能把差额归因（当时是 61s 页面空白 + 68s 无效下载），否则只能靠猜。
"""

import asyncio

from src.browser.detector import self_reported_work_seconds


class _FakePage:
    def __init__(self, text: str, *, fail: bool = False) -> None:
        self._text = text
        self._fail = fail

    async def evaluate(self, script, arg=None):
        if self._fail:
            raise RuntimeError("evaluate failed")
        return self._text


def _read(text: str, **kwargs) -> int | None:
    return asyncio.run(self_reported_work_seconds(_FakePage(text, **kwargs)))


def test_reads_seconds_only_label():
    assert _read("Worked for 10s" + chr(10) + "已处理为 800×800 px") == 10


def test_reads_minutes_and_seconds():
    # 生成 3 页 Word 实测 "Worked for 4m 49s"（289s）。
    assert _read("Worked for 4m 49s") == 289


def test_reads_thought_for_and_chinese_variants():
    assert _read("Thought for 12s") == 12
    assert _read("已思考 8 秒") == 8
    assert _read("已思考 2 分 5 秒") == 125


def test_plain_reply_without_a_label_reports_nothing():
    assert _read("已处理为 800×800 px，保持原图比例。") is None


def test_never_raises_when_the_page_is_unusable():
    assert _read("", fail=True) is None
