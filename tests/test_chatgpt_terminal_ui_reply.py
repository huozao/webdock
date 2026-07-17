from __future__ import annotations

import asyncio

import src.browser.chatgpt_page as chatgpt_page
from src.browser.chatgpt_page import ChatGPTPage
from src.utils.errors import ErrorCode, RelayError


class DummyPage:
    pass


def test_feishu_ask_rejects_stopped_thinking_markdown(monkeypatch):
    async def false_any(*_args, **_kwargs):
        return False

    async def find_selector(*_args, **_kwargs):
        return "selector"

    async def noop(*_args, **_kwargs):
        return None

    async def zero(*_args, **_kwargs):
        return 0

    async def empty_list(*_args, **_kwargs):
        return []

    async def empty_text(*_args, **_kwargs):
        return ""

    async def wait_done(*_args, **_kwargs):
        return "initial answer"

    async def stopped_markdown(*_args, **_kwargs):
        return "Stopped thinking\nEdit"

    monkeypatch.setattr(chatgpt_page, "any_selector_found", false_any)
    monkeypatch.setattr(chatgpt_page, "find_first", find_selector)
    monkeypatch.setattr(chatgpt_page, "random_delay", noop)
    monkeypatch.setattr(chatgpt_page, "paste_text", noop)
    monkeypatch.setattr(chatgpt_page, "hover_and_click", noop)
    monkeypatch.setattr(chatgpt_page, "assistant_message_count", zero)
    monkeypatch.setattr(chatgpt_page, "rich_assistant_text", empty_text)
    monkeypatch.setattr(chatgpt_page, "generated_image_srcs", empty_list)
    monkeypatch.setattr(chatgpt_page, "mark_existing_reply_media", noop)
    monkeypatch.setattr(chatgpt_page, "latest_message_has_widget", false_any)
    monkeypatch.setattr(chatgpt_page, "wait_for_response_complete", wait_done)
    monkeypatch.setattr(chatgpt_page, "rich_assistant_markdown", stopped_markdown)
    monkeypatch.setattr(chatgpt_page, "save_debug_dump", lambda *_args, **_kwargs: asyncio.sleep(0, result="debug"))

    page = ChatGPTPage(DummyPage(), media_store=None, channel="feishu")

    try:
        asyncio.run(page.ask("hello", timeout_seconds=1, hard_timeout_seconds=1))
    except RelayError as exc:
        assert exc.code == ErrorCode.RESPONSE_TIMEOUT
    else:
        raise AssertionError("Stopped thinking UI text was returned as a normal reply")
