"""多图回复必须按"这一轮到底生成了几张"来交付。

2026-08-16 生产实测（探针逐帧）：一条 5 图请求在 169.5s 就把 5 张都画完了、
稳定到 192.0s；stop 按钮熄灭前后 ChatGPT 把其中 3 张重新拉了一遍，194.5s
的完成帧只剩 3 张，于是只投了 3 张。判定时机没错，错在只信完成那一帧。
"""

from __future__ import annotations

import asyncio

import src.browser.chatgpt_page as chatgpt_page
from src.browser.chatgpt_page import ChatGPTPage
from src.browser.detector import GeneratedImageWatch


def _scans(monkeypatch, frames: list[list[str]]) -> None:
    """generated_image_srcs 按 frames 依次返回，最后一帧之后保持不变。"""

    def scan(_page):
        frame = frames[0] if len(frames) == 1 else frames.pop(0)
        return asyncio.sleep(0, result=list(frame))

    monkeypatch.setattr(chatgpt_page, "generated_image_srcs", scan)


IMG = "https://chatgpt.com/backend-api/estuary/content?id=%s"
FIVE = [IMG % i for i in range(5)]


def test_collapsed_completion_frame_waits_for_the_full_set(monkeypatch):
    # 完成帧只剩 3 张（重渲窗口），随后恢复 5 张 —— 必须等回 5 张再交付。
    monkeypatch.setattr(chatgpt_page, "_IMAGE_SETTLE_POLL_SECONDS", 0.0)
    _scans(monkeypatch, [FIVE[:3], FIVE[:3], FIVE])
    watch = GeneratedImageWatch()
    watch.observe(FIVE)  # 等待循环里见过 5 张

    page = ChatGPTPage(page=object())
    got = asyncio.run(page._await_stable_generated_images(set(), watch))

    assert got == FIVE


def test_single_image_reply_does_not_pay_the_settle_window(monkeypatch):
    # 常见情况：一帧就够 -> 只扫一次就返回，不引入任何等待。
    calls = {"n": 0}

    def scan(_page):
        calls["n"] += 1
        return asyncio.sleep(0, result=[IMG % 0])

    monkeypatch.setattr(chatgpt_page, "generated_image_srcs", scan)
    watch = GeneratedImageWatch()
    watch.observe([IMG % 0])

    page = ChatGPTPage(page=object())

    assert asyncio.run(page._await_stable_generated_images(set(), watch)) == [IMG % 0]
    assert calls["n"] == 1


def test_union_tops_up_when_the_dom_never_recovers(monkeypatch):
    # DOM 永远回不到 5 张 -> 用见过最全的一帧 + 等待期并集补齐，宁可多抓也不少发。
    monkeypatch.setattr(chatgpt_page, "_IMAGE_SETTLE_POLL_SECONDS", 0.0)
    monkeypatch.setattr(chatgpt_page, "IMAGE_RESCAN_SECONDS", 0.05)
    _scans(monkeypatch, [FIVE[:2]])
    watch = GeneratedImageWatch()
    watch.observe(FIVE)

    page = ChatGPTPage(page=object())
    got = asyncio.run(page._await_stable_generated_images(set(), watch))

    assert got[:2] == FIVE[:2]
    assert sorted(got) == sorted(FIVE)


def test_pre_existing_images_stay_excluded(monkeypatch):
    # 会话里原本就有的图不算这一轮的产出。
    monkeypatch.setattr(chatgpt_page, "_IMAGE_SETTLE_POLL_SECONDS", 0.0)
    old = IMG % "old"
    _scans(monkeypatch, [[old] + FIVE])
    watch = GeneratedImageWatch()
    watch.observe(FIVE)

    page = ChatGPTPage(page=object())

    assert asyncio.run(page._await_stable_generated_images({old}, watch)) == FIVE


class FakeStore:
    def __init__(self) -> None:
        self.puts: list[bytes] = []

    def put(self, data: bytes, content_type: str = "image/png", filename: str | None = None) -> str:
        self.puts.append(data)
        return f"token{len(self.puts)}"


class FakePage:
    """evaluate() 返回每个 src 的 base64 内容（由 contents 决定）。"""

    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.fetched: list[str] = []

    async def evaluate(self, _js, src=None):
        self.fetched.append(src)
        import base64

        data = self.contents.get(src)
        return base64.b64encode(data).decode() if data else ""


def test_same_picture_under_two_srcs_is_delivered_once():
    # 重渲会让同一张图换 src；media_store 每次 put 都发新 token，
    # 不按内容去重就会把同一张图发两遍。
    body = b"PNGDATA" + b"x" * 2000
    page = FakePage({IMG % "a": body, IMG % "b": body, IMG % "c": b"other" + b"y" * 2000})
    store = FakeStore()
    chat = ChatGPTPage(page=page, media_store=store)

    tokens = asyncio.run(chat._capture_image_tokens([IMG % "a", IMG % "b", IMG % "c"]))

    assert len(tokens) == 2
    assert len(store.puts) == 2


def test_capture_delivers_every_picture_without_a_count_cap():
    # 2026-08-16 起出站不限张数：这一轮生成几张就投几张。
    contents = {IMG % i: bytes([i]) + b"z" * 2000 for i in range(23)}
    page = FakePage(contents)
    chat = ChatGPTPage(page=page, media_store=FakeStore())

    tokens = asyncio.run(chat._capture_image_tokens([IMG % i for i in range(23)]))

    assert len(tokens) == 23


def test_watch_records_high_water_and_union():
    watch = GeneratedImageWatch()
    watch.observe(FIVE)
    watch.observe(FIVE[:3])  # 塌陷帧不能把高水位拉低
    watch.observe([IMG % "late"])

    assert watch.max_count == 5
    assert watch.srcs == FIVE + [IMG % "late"]
