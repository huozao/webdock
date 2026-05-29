from __future__ import annotations

import asyncio

from src.browser.lane_scheduler import ChatLaneScheduler, LaneContext, build_lane_key


class FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def page_for_lane(self, lane: LaneContext):
        self.calls.append(lane.key)
        return f"page:{lane.key}"


async def fake_ask(page: object, message: str) -> tuple[str, float]:
    await asyncio.sleep(0.05)
    return f"{page}:{message}", 0.05


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


def test_lane_key_uses_wechat_account_chat_type_and_peer_id():
    lane = LaneContext.from_metadata({"wechat_account": "B", "chat_type": "group", "peer_id": "group-1"})

    assert lane.key == "wechat:B:group:group-1"
    assert lane.project == "WeChat-B"
    assert build_lane_key("B", "group", "group-1") == "wechat:B:group:group-1"


def test_lane_key_falls_back_to_default_values_for_legacy_requests():
    lane = LaneContext.from_metadata(None)

    assert lane.key == "wechat:default:private:default"
    assert lane.project == "WeChat-default"
