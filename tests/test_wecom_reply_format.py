from __future__ import annotations

from src.browser.chatgpt_page import _media_screenshot_selectors, _strip_media_noise
from src.browser.lane_scheduler import LaneContext


def test_wecom_image_reply_drops_english_edit_ui_noise():
    assert _strip_media_noise("Edit\n") == ""


def test_wecom_uses_rich_channel_table_screenshots():
    selectors = [selector for selector, _ in _media_screenshot_selectors("wecom")]
    assert any("table" in selector for selector in selectors)


def test_wecom_channel_is_preserved_for_reply_formatting():
    lane = LaneContext.from_metadata(
        {
            "channel": "wecom",
            "wechat_account": "company-b",
            "chat_type": "group",
            "peer_id": "group:wr_test",
        }
    )

    assert lane.channel == "wecom"
    assert lane.key == "wecom:company-b:group:group:wr_test"
