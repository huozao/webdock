from __future__ import annotations

from src.browser.chatgpt_page import _image_reply_text, _strip_media_noise


def test_image_reply_drops_text_that_just_repeats_previous_snapshot():
    # The image reply completed on a new image src, but the grabbed text is still
    # the PREVIOUS turn's weather reply (page didn't update text this turn).
    prev = "7时 17° 部分晴\n最高大约 29°C，白天适合户外。"
    assert _image_reply_text(prev, prev) == ""


def test_image_reply_keeps_genuinely_new_text():
    prev = "7时 17° 部分晴"
    assert _image_reply_text("这是为你生成的男士着装推荐", prev) == "这是为你生成的男士着装推荐"


def test_image_reply_with_only_noise_returns_empty():
    prev = "天气文本"
    assert _image_reply_text("正在生成图片…\n下载\n复制", prev) == ""


def test_image_reply_empty_previous_keeps_text():
    assert _image_reply_text("一只猫的插画", "") == "一只猫的插画"


def test_image_reply_drops_worked_for_and_preview_ui_lines():
    # The imagegen turn's residual UI text ("Worked for 1m 21s" thinking summary,
    # Preview/Edit overlay labels) must not ride along with the delivered picture.
    assert _image_reply_text("Worked for 1m 21s\nEdit", "") == ""
    assert _image_reply_text("已思考 1m 40s\nPreview", "") == ""


def test_image_reply_compares_after_noise_strip():
    # Snapshot and current carry different UI noise lines but the SAME real text;
    # after stripping noise they match, so it's recognized as a repeat and dropped.
    prev = "下载\n7时 17° 部分晴\n最高 29°C"
    cur = "复制\n7时 17° 部分晴\n最高 29°C"
    assert _strip_media_noise(prev) == _strip_media_noise(cur)
    assert _image_reply_text(cur, prev) == ""
