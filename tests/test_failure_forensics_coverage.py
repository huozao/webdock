"""失败路径的取证覆盖。

契约见 AliECS/docs/constraints/browser-capture-evidence.md〈失败必截图〉。
2026-09-09 的跨机尝试链 webdock2:UPLOAD_FAILED → webdock1:BROWSER_NOT_STARTED 里，
第一环一张快照都没有——composer 当时长什么样只能靠猜。
"""
from __future__ import annotations

import asyncio

import pytest

from src.browser import lane_scheduler
from src.browser.lane_scheduler import ChatLaneScheduler, LaneContext
from src.utils.errors import ErrorCode, RelayError


class FakeBrowser:
    async def page_for_lane(self, lane: LaneContext):
        return f"page:{lane.key}"


def _lane():
    return LaneContext.from_metadata(
        {"wechat_account": "A", "chat_type": "private", "peer_id": "user-1"})


def test_upload_failure_leaves_a_snapshot(monkeypatch):
    dumped: list[object] = []

    async def fake_dump(page, error):
        dumped.append((page, error))
        return "logs/debug/2026-09-09_120000"

    async def failing_upload(page, images):
        return 0

    async def never_ask(page, message):
        raise AssertionError("上传失败时不得把残缺的一轮发出去")

    monkeypatch.setattr(lane_scheduler, "save_debug_dump", fake_dump)
    scheduler = ChatLaneScheduler(
        max_concurrent_chats=1, ask_func=never_ask, image_uploader=failing_upload)

    with pytest.raises(RelayError) as caught:
        asyncio.run(scheduler.ask(FakeBrowser(), _lane(), "改图", images=["data:image/png;base64,AA"]))

    assert caught.value.code == ErrorCode.UPLOAD_FAILED
    assert caught.value.debug_dir == "logs/debug/2026-09-09_120000"
    assert len(dumped) == 1


def test_hard_cap_timeout_dumps_before_the_lane_is_rebuilt(monkeypatch):
    """⚠️ 顺序判据：重建之后这条车道的标签页是全新的，那时截的图与出问题的那轮无关。"""
    order: list[str] = []

    async def fake_dump(page, error):
        order.append("dump")
        return "logs/debug/2026-09-09_130000"

    async def fake_reset(browser, lane):
        order.append("reset")

    async def hanging_ask(page, message):
        await asyncio.sleep(3600)

    monkeypatch.setattr(lane_scheduler, "save_debug_dump", fake_dump)
    monkeypatch.setattr(lane_scheduler, "_reset_lane_page", fake_reset)
    scheduler = ChatLaneScheduler(
        max_concurrent_chats=1, ask_func=hanging_ask,
        chat_timeout_seconds=0, request_hard_cap_seconds=0)

    with pytest.raises(RelayError) as caught:
        asyncio.run(scheduler.ask(FakeBrowser(), _lane(), "很长的任务"))

    assert caught.value.code == ErrorCode.RESPONSE_TIMEOUT
    assert order == ["dump", "reset"]
    assert caught.value.debug_dir == "logs/debug/2026-09-09_130000"


def test_forensics_failure_never_masks_the_original_error(monkeypatch):
    """取证自己炸了，也必须把原来那个错误原样抛出去。"""
    async def exploding_dump(page, error):
        raise RuntimeError("CDP gone")

    async def failing_upload(page, images):
        return 0

    async def never_ask(page, message):
        raise AssertionError("不应到达")

    monkeypatch.setattr(lane_scheduler, "save_debug_dump", exploding_dump)
    scheduler = ChatLaneScheduler(
        max_concurrent_chats=1, ask_func=never_ask, image_uploader=failing_upload)

    with pytest.raises(RelayError) as caught:
        asyncio.run(scheduler.ask(FakeBrowser(), _lane(), "改图", images=["data:image/png;base64,AA"]))

    assert caught.value.code == ErrorCode.UPLOAD_FAILED
    assert caught.value.debug_dir is None
