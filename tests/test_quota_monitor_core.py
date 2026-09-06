from __future__ import annotations

import sqlite3

from quota_monitor.core import event_key, record_event_once, reset_candidate, screenshot_url, weekly_reset_candidate


def test_reset_requires_healthy_previous_and_large_jump():
    old = {"status": "healthy", "fields": {"remaining": "10", "limit": "100"}}
    new = {"status": "healthy", "fields": {"remaining": "90", "limit": "100"}}
    assert reset_candidate(new, old)
    assert not reset_candidate({"status": "schema_changed", "fields": {}}, old)


def test_event_is_idempotent_and_screenshot_url_is_same_origin():
    conn = sqlite3.connect(":memory:")
    assert record_event_once(conn, event_key("codex", {"reset_at": "tomorrow"}), "codex", "now", {})
    assert not record_event_once(conn, event_key("codex", {"reset_at": "tomorrow"}), "codex", "now", {})
    assert screenshot_url(3) == "/v1/quota/captures/3/screenshot"


def test_only_weekly_window_can_trigger_reset_alert():
    old = {"status": "healthy", "fields": {"remaining": "20%", "weekly_remaining": "10%", "weekly_reset_at": "tomorrow"}}
    new = {"status": "healthy", "fields": {"remaining": "100%", "weekly_remaining": "100%", "weekly_reset_at": "next-week"}}
    assert weekly_reset_candidate(new, old)

    five_hour_only = {"status": "healthy", "fields": {"remaining": "100%", "weekly_remaining": "10%", "weekly_reset_at": "tomorrow"}}
    assert not weekly_reset_candidate(five_hour_only, old)
