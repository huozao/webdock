from __future__ import annotations

import asyncio

import src.browser.chatgpt_page as chatgpt_page
from src.browser.chatgpt_page import ChatGPTPage, _image_reply_text, _strip_media_noise


def test_settling_rescan_picks_up_the_image_that_lands_after_the_wait(monkeypatch):
    # Measured 2026-08-14: an image-edit turn flips its generated src 1 → 0 → 1
    # while ChatGPT swaps the rendered picture in. A single scan can land on the
    # empty frame, so the scaffold gate drives a bounded re-scan.
    scans = [[], ["https://chatgpt.com/backend-api/estuary/content?id=new"]]
    monkeypatch.setattr(chatgpt_page, "imagegen_pending", lambda _page: asyncio.sleep(0, result=True))
    monkeypatch.setattr(
        chatgpt_page, "generated_image_srcs", lambda _page: asyncio.sleep(0, result=scans.pop(0))
    )
    monkeypatch.setattr(chatgpt_page, "IMAGE_RESCAN_SECONDS", 5.0)

    page = ChatGPTPage(page=object())
    found = asyncio.run(page._await_settling_generated_images(set()))

    assert found == ["https://chatgpt.com/backend-api/estuary/content?id=new"]


def test_settling_rescan_is_skipped_without_a_pending_scaffold(monkeypatch):
    # A plain text reply must not pay the re-scan window.
    monkeypatch.setattr(chatgpt_page, "imagegen_pending", lambda _page: asyncio.sleep(0, result=False))

    def fail(_page):  # pragma: no cover - must not be reached
        raise AssertionError("no image scan expected without a pending scaffold")

    monkeypatch.setattr(chatgpt_page, "generated_image_srcs", fail)

    page = ChatGPTPage(page=object())

    assert asyncio.run(page._await_settling_generated_images(set())) == []


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
    assert _image_reply_text("Share\nDownload\nSave", "") == ""


def test_image_reply_compares_after_noise_strip():
    # Snapshot and current carry different UI noise lines but the SAME real text;
    # after stripping noise they match, so it's recognized as a repeat and dropped.
    prev = "下载\n7时 17° 部分晴\n最高 29°C"
    cur = "复制\n7时 17° 部分晴\n最高 29°C"
    assert _strip_media_noise(prev) == _strip_media_noise(cur)
    assert _image_reply_text(cur, prev) == ""
