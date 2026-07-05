from __future__ import annotations

import asyncio

from src.browser.lane_scheduler import (
    ChatLaneScheduler,
    LaneContext,
    build_lane_key,
    select_chat_timeout,
)
from src.config import Settings
from src.utils.errors import ErrorCode, RelayError


class FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def page_for_lane(self, lane: LaneContext):
        self.calls.append(lane.key)
        return f"page:{lane.key}"


async def fake_ask(page: object, message: str) -> tuple[str, float]:
    await asyncio.sleep(0.05)
    return f"{page}:{message}", 0.05


class FakeRouter:
    """Minimal LaneRouter stand-in: only the peers in `configured` route."""

    def __init__(self, configured: set[str]) -> None:
        self._configured = set(configured)
        self.conversations: dict[str, str] = {}

    def is_configured(self, peer_id: str | None) -> bool:
        return peer_id in self._configured

    def resolve_target_url(self, peer_id: str | None, *, force_new: bool = False) -> str | None:
        if peer_id not in self._configured:
            return None
        if force_new:
            return f"https://chatgpt.com/g/proj-{peer_id}"
        return self.conversations.get(peer_id) or f"https://chatgpt.com/g/proj-{peer_id}"

    def record_conversation_url(self, peer_id: str | None, url: str | None) -> None:
        if peer_id in self._configured and url:
            self.conversations[peer_id] = url

    def clear_conversation(self, peer_id: str | None) -> None:
        self.conversations.pop(peer_id, None)


def test_scheduler_allows_three_wechat_accounts_to_run_concurrently():
    asyncio.run(_run_three_account_concurrency_case())


async def _run_three_account_concurrency_case():
    scheduler = ChatLaneScheduler(max_concurrent_chats=3, ask_func=fake_ask)
    browser = FakeBrowser()
    started = asyncio.Event()
    release = asyncio.Event()
    running = 0
    max_running = 0

    async def blocking_ask(page: object, message: str) -> tuple[str, float]:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        if running == 3:
            started.set()
        await release.wait()
        running -= 1
        return f"{page}:{message}", 0.1

    scheduler = ChatLaneScheduler(max_concurrent_chats=3, ask_func=blocking_ask)
    tasks = [
        asyncio.create_task(
            scheduler.ask(
                browser,
                LaneContext.from_metadata(
                    {"wechat_account": account, "chat_type": "private", "peer_id": "user-1"}
                ),
                f"hello {account}",
            )
        )
        for account in ("A", "B", "C")
    ]

    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    await asyncio.gather(*tasks)

    assert max_running == 3
    assert set(browser.calls) == {
        "wechat:A:private:user-1",
        "wechat:B:private:user-1",
        "wechat:C:private:user-1",
    }


def test_scheduler_serializes_messages_within_the_same_lane():
    asyncio.run(_run_same_lane_serial_case())


async def _run_same_lane_serial_case():
    order: list[str] = []

    async def ordered_ask(page: object, message: str) -> tuple[str, float]:
        order.append(f"start:{message}")
        await asyncio.sleep(0.02)
        order.append(f"end:{message}")
        return str(page), 0.02

    scheduler = ChatLaneScheduler(max_concurrent_chats=3, ask_func=ordered_ask)
    browser = FakeBrowser()
    lane = LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "user-1"})

    await asyncio.gather(
        scheduler.ask(browser, lane, "first"),
        scheduler.ask(browser, lane, "second"),
    )

    assert order == ["start:first", "end:first", "start:second", "end:second"]


def test_metadata_less_request_inherits_recent_active_lane():
    asyncio.run(_run_inherit_case())


async def _run_inherit_case():
    router = FakeRouter(configured={"user-1"})
    scheduler = ChatLaneScheduler(max_concurrent_chats=3, ask_func=fake_ask, router=router)
    browser = FakeBrowser()

    # Text carries metadata -> configured lane.
    text_lane = LaneContext.from_metadata(
        {"wechat_account": "A", "chat_type": "private", "peer_id": "user-1"}
    )
    await scheduler.ask(browser, text_lane, "把这张头像改成第二张的风格")

    # The image arrives as its own request WITHOUT metadata -> default sentinel.
    image_lane = LaneContext.from_metadata(None)
    await scheduler.ask(browser, image_lane, "[image]")

    # Both ran on the same configured lane page, not a stray default page.
    assert browser.calls == ["wechat:A:private:user-1", "wechat:A:private:user-1"]


def test_metadata_less_request_falls_back_to_default_after_window():
    asyncio.run(_run_window_expiry_case())


async def _run_window_expiry_case():
    router = FakeRouter(configured={"user-1"})
    scheduler = ChatLaneScheduler(
        max_concurrent_chats=3, ask_func=fake_ask, router=router, lane_fallback_window_seconds=0.0
    )
    browser = FakeBrowser()

    await scheduler.ask(
        browser,
        LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "user-1"}),
        "hi",
    )
    await scheduler.ask(browser, LaneContext.from_metadata(None), "[image]")

    # Window elapsed -> no inheritance, metadata-less request stays on default.
    assert browser.calls == ["wechat:A:private:user-1", "wechat:default:private:default"]


def test_scheduler_uploads_images_before_asking():
    asyncio.run(_run_upload_case())


async def _run_upload_case():
    calls: list[tuple[object, list[str]]] = []

    async def fake_upload(page: object, images: list[str]) -> int:
        calls.append((page, list(images)))
        return len(images)

    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=fake_ask, image_uploader=fake_upload)
    lane = LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "user-1"})

    await scheduler.ask(FakeBrowser(), lane, "改图", images=["data:image/png;base64,AAAA"])

    assert calls == [("page:wechat:A:private:user-1", ["data:image/png;base64,AAAA"])]


def test_scheduler_skips_upload_when_no_images():
    asyncio.run(_run_no_upload_case())


async def _run_no_upload_case():
    calls: list[list[str]] = []

    async def fake_upload(page: object, images: list[str]) -> int:
        calls.append(list(images))
        return 0

    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=fake_ask, image_uploader=fake_upload)

    await scheduler.ask(FakeBrowser(), LaneContext.from_metadata(None), "just text")

    assert calls == []


def test_lane_key_uses_wechat_account_chat_type_and_peer_id():
    lane = LaneContext.from_metadata({"wechat_account": "B", "chat_type": "group", "peer_id": "group-1"})

    assert lane.key == "wechat:B:group:group-1"
    assert lane.project == "WeChat-B"
    assert build_lane_key("B", "group", "group-1") == "wechat:B:group:group-1"


def test_lane_key_falls_back_to_default_values_for_legacy_requests():
    lane = LaneContext.from_metadata(None)

    assert lane.key == "wechat:default:private:default"
    assert lane.project == "WeChat-default"


def test_close_idle_lanes_closes_only_idle_and_unlocked_tabs():
    asyncio.run(_run_idle_reaper_case())


async def _run_idle_reaper_case():
    import time as _time

    closed_keys: list[str] = []

    class ClosingBrowser(FakeBrowser):
        async def close_lane_page(self, lane_key: str) -> bool:
            closed_keys.append(lane_key)
            return True

    scheduler = ChatLaneScheduler(max_concurrent_chats=3, ask_func=fake_ask)
    browser = ClosingBrowser()
    lane_a = LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "u1"})
    lane_b = LaneContext.from_metadata({"wechat_account": "B", "chat_type": "private", "peer_id": "u2"})
    await scheduler.ask(browser, lane_a, "hi")
    await scheduler.ask(browser, lane_b, "hi")

    now = _time.monotonic()
    scheduler._lane_last_active[lane_a.key] = now - 10_000  # idle
    scheduler._lane_last_active[lane_b.key] = now           # just active

    closed = await scheduler.close_idle_lanes(browser, idle_seconds=1000, now=now)

    assert closed == [lane_a.key]
    assert closed_keys == [lane_a.key]
    assert lane_b.key not in closed  # recently active -> kept


def test_close_idle_lanes_skips_a_lane_currently_in_use():
    asyncio.run(_run_idle_reaper_locked_case())


async def _run_idle_reaper_locked_case():
    import time as _time

    closed_keys: list[str] = []

    class ClosingBrowser(FakeBrowser):
        async def close_lane_page(self, lane_key: str) -> bool:
            closed_keys.append(lane_key)
            return True

    scheduler = ChatLaneScheduler(max_concurrent_chats=3, ask_func=fake_ask)
    browser = ClosingBrowser()
    lane = LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "u1"})
    await scheduler.ask(browser, lane, "hi")

    now = _time.monotonic()
    scheduler._lane_last_active[lane.key] = now - 10_000  # idle by clock
    lock = await scheduler._get_lane_lock(lane.key)
    await lock.acquire()  # simulate an in-flight request holding the lane
    try:
        closed = await scheduler.close_idle_lanes(browser, idle_seconds=1000, now=now)
    finally:
        lock.release()

    assert closed == []  # in-use lane is never closed mid-request
    assert closed_keys == []


def test_active_reply_past_soft_timeout_is_not_killed_before_hard_cap():
    asyncio.run(_run_active_reply_case())


async def _run_active_reply_case():
    # An actively-progressing reply that runs LONGER than the soft timeout but
    # finishes before the hard cap must NOT be cut off (the soft timeout is an
    # idle deadline, not a wall-clock kill). Regression guard: a previous build set
    # the hard cap to soft+margin and killed live reasoning replies at ~135s.
    async def slow_progressing_ask(page: object, message: str) -> tuple[str, float]:
        await asyncio.sleep(0.3)  # > soft (0.05), < hard cap (2.0)
        return "done", 0.3

    scheduler = ChatLaneScheduler(
        max_concurrent_chats=1,
        ask_func=slow_progressing_ask,
        chat_timeout_seconds=0.05,
        chat_timeout_seconds_with_images=0.05,
        request_hard_cap_seconds=2.0,
    )
    lane = LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "u1"})

    answer, _ = await scheduler.ask(FakeBrowser(), lane, "hi")
    assert answer == "done"


def test_select_chat_timeout_uses_larger_value_for_image_requests():
    # Image-bearing turns (e.g. 图片生成) legitimately run longer than text.
    assert select_chat_timeout(120, 300, has_images=False) == 120
    assert select_chat_timeout(120, 300, has_images=True) == 300


def test_settings_has_image_timeout_default():
    assert Settings().chat_timeout_seconds_with_images == 300


def test_scheduler_recovers_lane_and_raises_when_a_request_hangs():
    asyncio.run(_run_hang_recovery_case())


async def _run_hang_recovery_case():
    reset_calls: list[str] = []
    archived_errors: list[object] = []

    class HangBrowser:
        async def page_for_lane(self, lane: LaneContext):
            return f"page:{lane.key}"

        async def reset_lane_page(self, lane: LaneContext):
            reset_calls.append(lane.key)
            return f"page:{lane.key}"

    async def archiver(lane, inbound, images, *, answer=None, duration=None, error=None, kind="ask"):
        if error is not None:
            archived_errors.append(error)

    async def hanging_ask(page: object, message: str) -> tuple[str, float]:
        await asyncio.sleep(10)
        return "never", 0.0

    scheduler = ChatLaneScheduler(
        max_concurrent_chats=1,
        ask_func=hanging_ask,
        archiver=archiver,
        chat_timeout_seconds=0.05,
        chat_timeout_seconds_with_images=0.05,
        request_hard_cap_seconds=0.05,
    )
    lane = LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "u1"})

    raised: RelayError | None = None
    try:
        await scheduler.ask(HangBrowser(), lane, "hi")
    except RelayError as exc:
        raised = exc

    # A hung request must NOT hang forever holding the slot: it times out, the
    # lane tab is rebuilt (so the next request is clean), and the failure is
    # archived — never wedging the whole process / other users.
    assert raised is not None and raised.code == ErrorCode.RESPONSE_TIMEOUT
    assert reset_calls == ["wechat:A:private:u1"]
    assert archived_errors and archived_errors[0] is not None


def test_lane_context_parses_chatgpt_mode():
    lane = LaneContext.from_metadata(
        {"channel": "feishu", "peer_id": "user:ou_x", "chatgpt_mode": "fast"}
    )
    assert lane.chatgpt_mode == "fast"


def test_lane_context_rejects_unknown_chatgpt_mode():
    lane = LaneContext.from_metadata(
        {"channel": "feishu", "peer_id": "user:ou_x", "chatgpt_mode": "turbo"}
    )
    assert lane.chatgpt_mode is None


def test_lane_context_chatgpt_mode_defaults_none():
    lane = LaneContext.from_metadata({"channel": "feishu", "peer_id": "user:ou_x"})
    assert lane.chatgpt_mode is None


def test_default_ask_forwards_mode(monkeypatch):
    captured = {}

    class FakeChatPage:
        def __init__(self, page, media_store=None, channel="wechat"):
            captured["channel"] = channel

        async def ask(self, message, *, timeout_seconds=None, hard_timeout_seconds=None, mode=None):
            captured["mode"] = mode
            return "ok", 0.1

    import src.browser.lane_scheduler as lane_scheduler_module

    monkeypatch.setattr(lane_scheduler_module, "ChatGPTPage", FakeChatPage)
    scheduler = ChatLaneScheduler(max_concurrent_chats=1)
    asyncio.run(scheduler._default_ask(object(), "hi", "feishu", mode="fast"))
    assert captured["mode"] == "fast"
    assert captured["channel"] == "feishu"
