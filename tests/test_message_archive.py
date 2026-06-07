from __future__ import annotations

import asyncio
import json

import src.browser.message_archive as message_archive
from src.browser.lane_scheduler import ChatLaneScheduler, LaneContext
from src.config import Settings
from src.utils.errors import ErrorCode, RelayError


class FakeBrowser:
    async def page_for_lane(self, lane: LaneContext):
        return f"page:{lane.key}"


def _settings(tmp_path, enabled=True):
    return Settings(archive_dir=tmp_path, archive_enabled=enabled)


def _read_records(archive_dir):
    files = list(archive_dir.glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def test_archive_writes_full_success_record(tmp_path, monkeypatch):
    monkeypatch.setattr(message_archive, "get_settings", lambda: _settings(tmp_path))
    lane = LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "user-1"})

    asyncio.run(message_archive.archive_exchange(lane, "你好", None, answer="完整的回复正文", duration=12.3))

    rec = _read_records(tmp_path)[0]
    assert rec["status"] == "ok"
    assert rec["kind"] == "ask"
    assert rec["lane"]["key"] == "wechat:A:private:user-1"
    assert rec["lane"]["peer_id"] == "user-1"
    assert rec["inbound"]["text"] == "你好"
    assert rec["inbound"]["chars"] == 2
    assert rec["inbound"]["images"] == 0
    assert rec["outbound"]["text"] == "完整的回复正文"
    assert rec["outbound"]["chars"] == 7
    assert rec["outbound"]["duration_seconds"] == 12.3


def test_archive_writes_error_record_with_debug_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(message_archive, "get_settings", lambda: _settings(tmp_path))
    lane = LaneContext.from_metadata(None)
    err = RelayError(ErrorCode.RESPONSE_TIMEOUT, "did not finish", debug_dir="logs/debug/2026-06-08_120000")

    asyncio.run(message_archive.archive_exchange(lane, "慢问题", None, error=err))

    rec = _read_records(tmp_path)[0]
    assert rec["status"] == "error"
    assert rec["error"]["code"] == "RESPONSE_TIMEOUT"
    assert rec["error"]["message"] == "did not finish"
    assert rec["error"]["debug_dir"] == "logs/debug/2026-06-08_120000"
    assert "outbound" not in rec


def test_archive_disabled_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(message_archive, "get_settings", lambda: _settings(tmp_path, enabled=False))
    lane = LaneContext.from_metadata(None)

    asyncio.run(message_archive.archive_exchange(lane, "hi", None, answer="ok", duration=1.0))

    assert list(tmp_path.glob("*.jsonl")) == []


def test_archive_failure_never_raises(monkeypatch):
    # A broken settings access must be swallowed — archiving can't break a chat.
    def boom():
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(message_archive, "get_settings", boom)
    lane = LaneContext.from_metadata(None)
    # Should not raise.
    asyncio.run(message_archive.archive_exchange(lane, "hi", None, answer="ok", duration=1.0))


def test_scheduler_archives_success_then_error():
    asyncio.run(_run_scheduler_archive_case())


async def _run_scheduler_archive_case():
    calls: list[dict] = []

    async def fake_archiver(lane, inbound, images, *, answer=None, duration=None, error=None, kind="ask"):
        calls.append({"inbound": inbound, "answer": answer, "error": error, "kind": kind})

    async def ok_ask(page, message):
        return "full reply", 0.1

    lane = LaneContext.from_metadata({"wechat_account": "A", "chat_type": "private", "peer_id": "user-1"})

    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=ok_ask, archiver=fake_archiver)
    answer, _ = await scheduler.ask(FakeBrowser(), lane, "hi")
    assert answer == "full reply"
    assert len(calls) == 1
    assert calls[0]["answer"] == "full reply"
    assert calls[0]["error"] is None

    calls.clear()

    async def boom_ask(page, message):
        raise RelayError(ErrorCode.RESPONSE_TIMEOUT, "timeout")

    scheduler2 = ChatLaneScheduler(max_concurrent_chats=1, ask_func=boom_ask, archiver=fake_archiver)
    raised = False
    try:
        await scheduler2.ask(FakeBrowser(), lane, "slow")
    except RelayError:
        raised = True
    assert raised
    assert len(calls) == 1
    assert calls[0]["error"] is not None
    assert calls[0]["answer"] is None
