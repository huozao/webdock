"""纯函数与持久化辅助，供 quota-monitor API 使用。

这里不包含浏览器控制；登录和验证永远由 noVNC 人工完成，采集器只在
``ATTACH_ENABLED`` 明确开启后读取页面。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def reset_candidate(current: dict[str, Any], previous: dict[str, Any] | None) -> bool:
    if not previous:
        return False
    now, old = current.get("fields", {}), previous.get("fields", {})
    if current.get("status") != "healthy" or previous.get("status") != "healthy":
        return False
    if now.get("reset_at") and old.get("reset_at") and now["reset_at"] != old["reset_at"]:
        return True
    try:
        remaining = float(str(now["remaining"]).replace(",", "").rstrip("%"))
        old_remaining = float(str(old["remaining"]).replace(",", "").rstrip("%"))
    except (KeyError, TypeError, ValueError):
        return False
    try:
        limit = float(str(now.get("limit", old.get("limit"))).replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return False
    return remaining > old_remaining and remaining - old_remaining >= max(10.0, limit * 0.2)


def weekly_reset_candidate(current: dict[str, Any], previous: dict[str, Any] | None) -> bool:
    """Only detect weekly-window resets; 5-hour window changes are not alerts."""
    if not previous or current.get("status") != "healthy" or previous.get("status") != "healthy":
        return False
    now, old = current.get("fields", {}), previous.get("fields", {})
    if now.get("weekly_reset_at") and old.get("weekly_reset_at") and now["weekly_reset_at"] != old["weekly_reset_at"]:
        return True
    try:
        remaining = float(str(now["weekly_remaining"]).replace(",", "").rstrip("%"))
        old_remaining = float(str(old["weekly_remaining"]).replace(",", "").rstrip("%"))
    except (KeyError, TypeError, ValueError):
        return False
    return remaining > old_remaining and remaining - old_remaining >= 20.0


def event_key(provider: str, fields: dict[str, Any]) -> str:
    marker = (fields.get("weekly_reset_at") or fields.get("reset_at") or
              fields.get("weekly_remaining") or fields.get("remaining") or "unknown")
    window = fields.get("weekly_window") or fields.get("window", "")
    return f"{provider}:{window}:{marker}"


def record_event_once(conn: sqlite3.Connection, key: str, provider: str, detected_at: str,
                      payload: dict[str, Any]) -> bool:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quota_events (event_key TEXT PRIMARY KEY, provider TEXT NOT NULL, detected_at TEXT NOT NULL, payload_json TEXT NOT NULL)"
    )
    cursor = conn.execute(
        "INSERT OR IGNORE INTO quota_events(event_key,provider,detected_at,payload_json) VALUES(?,?,?,?)",
        (key, provider, detected_at, json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()
    return cursor.rowcount == 1


def screenshot_url(capture_id: int, *, prefix: str = "/v1/quota/captures") -> str:
    """Return a same-origin URL; callers should expose it behind Authelia."""
    return f"{prefix}/{int(capture_id)}/screenshot"
