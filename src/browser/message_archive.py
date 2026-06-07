from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import json

from src.config import get_settings

log = logging.getLogger(__name__)


async def archive_exchange(
    lane: Any,
    inbound_text: str,
    images: list[str] | None = None,
    *,
    answer: str | None = None,
    duration: float | None = None,
    error: BaseException | None = None,
    kind: str = "ask",
) -> None:
    """Append one full inbound/outbound exchange to the daily archive.

    Records EVERY WeChat→ChatGPT round-trip (success and failure) so the full
    sent/received text is kept on the laptop for auditing "微信只收到一半回复"
    style issues. On error the record points at the DOM snapshot directory that
    save_debug_dump already wrote (page.html / screenshot.png), so a bad reply can
    be reproduced from the captured page.

    One JSON object per line (JSONL) under logs/archive/<YYYY-MM-DD>.jsonl. The
    write is plain synchronous I/O with no await between open and close, so within
    the single event loop it is atomic against other coroutines (no interleaving,
    no lock needed). Best-effort: any failure is swallowed — archiving must never
    break a chat.
    """
    try:
        settings = get_settings()
        if not settings.archive_enabled:
            return
        record = _build_record(lane, inbound_text, images, answer, duration, error, kind)
        line = json.dumps(record, ensure_ascii=False)
        archive_dir = settings.archive_dir
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception as exc:  # never let archiving break the chat
        log.warning("message archive write failed: %s", exc)


def _build_record(
    lane: Any,
    inbound_text: str,
    images: list[str] | None,
    answer: str | None,
    duration: float | None,
    error: BaseException | None,
    kind: str,
) -> dict[str, Any]:
    inbound = inbound_text or ""
    record: dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "kind": kind,
        "lane": {
            "key": getattr(lane, "key", ""),
            "wechat_account": getattr(lane, "wechat_account", ""),
            "chat_type": getattr(lane, "chat_type", ""),
            "peer_id": getattr(lane, "peer_id", ""),
            "project": getattr(lane, "project", ""),
            "target_url": getattr(lane, "target_url", None),
        },
        "inbound": {
            "text": inbound,
            "chars": len(inbound),
            "images": len(images or []),
        },
    }
    if error is not None:
        code = getattr(error, "code", None)
        record["status"] = "error"
        record["error"] = {
            "code": getattr(code, "value", str(code) if code is not None else type(error).__name__),
            "message": getattr(error, "message", str(error)),
            "debug_dir": getattr(error, "debug_dir", None),
        }
    else:
        out = answer or ""
        record["status"] = "ok"
        record["outbound"] = {
            "text": out,
            "chars": len(out),
            "duration_seconds": duration,
        }
    return record
