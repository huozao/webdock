from __future__ import annotations

import asyncio
import json
import os

from src.browser.lane_routing import (
    LaneRouter,
    NEW_CONVERSATION_ACK,
    is_conversation_url,
    parse_new_conversation_trigger,
)
from src.browser.lane_scheduler import ChatLaneScheduler, LaneContext


PROJECT_A = "https://chatgpt.com/g/g-p-aaaa-weixin-a/project"
CONV_A = "https://chatgpt.com/g/g-p-aaaa-weixin-a/c/conv-1"
CONV_A2 = "https://chatgpt.com/g/g-p-aaaa-weixin-a/c/conv-2"


def _write_config(path, lanes):
    path.write_text(json.dumps({"lanes": lanes}, ensure_ascii=False), encoding="utf-8")


def _router(tmp_path, lanes):
    config = tmp_path / "wechat_projects.json"
    _write_config(config, lanes)
    return LaneRouter(config_path=config, state_path=tmp_path / "lane_state.json")


# ---- trigger parsing ----

def test_parse_trigger_variants():
    assert parse_new_conversation_trigger("/新对话") == (True, "")
    assert parse_new_conversation_trigger("/新对话 你好") == (True, "你好")
    assert parse_new_conversation_trigger("  /新对话   多空格  ") == (True, "多空格")
    assert parse_new_conversation_trigger("你好") == (False, "你好")
    # boundary: a longer word that merely starts with the trigger is NOT a trigger
    assert parse_new_conversation_trigger("/新对话题目") == (False, "/新对话题目")


def test_config_hot_reloads_when_file_changes(tmp_path):
    """The routing puller rewrites the config file when the control-plane sheet
    changes; a long-running LaneRouter must pick up the new project_url without a
    restart (so /新对话 opens the NEW project)."""
    config = tmp_path / "wechat_projects.json"
    _write_config(config, {"A": {"name": "微信A", "project_url": PROJECT_A}})
    router = LaneRouter(config_path=config, state_path=tmp_path / "lane_state.json")

    assert router.resolve_target_url("A", force_new=True) == PROJECT_A

    new_project = "https://chatgpt.com/g/g-p-bbbb-weixin-a/project"
    _write_config(config, {"A": {"name": "微信A", "project_url": new_project}})
    os.utime(config, (config.stat().st_atime, config.stat().st_mtime + 5))  # ensure mtime change is seen

    assert router.resolve_target_url("A", force_new=True) == new_project
    assert router.is_configured("A") is True


def test_is_conversation_url():
    assert is_conversation_url("https://chatgpt.com/g/g-p-x/c/abc")
    assert is_conversation_url("https://chatgpt.com/c/abc")
    assert not is_conversation_url("https://chatgpt.com/g/g-p-x/project")
    assert not is_conversation_url("https://chatgpt.com/")
    assert not is_conversation_url(None)


# ---- resolve / record / clear ----

def test_resolve_prefers_conversation_then_project(tmp_path):
    router = _router(tmp_path, {"A": {"name": "微信A", "project_url": PROJECT_A}})

    # first time: no saved conversation -> project home
    assert router.resolve_target_url("A") == PROJECT_A
    # unconfigured peer -> None (fallback)
    assert router.resolve_target_url("Z") is None
    assert router.resolve_target_url(None) is None

    # after recording a conversation -> resume it
    router.record_conversation_url("A", CONV_A)
    assert router.resolve_target_url("A") == CONV_A
    # force_new ignores the saved conversation -> project home
    assert router.resolve_target_url("A", force_new=True) == PROJECT_A


def test_state_persists_across_instances(tmp_path):
    config = tmp_path / "wechat_projects.json"
    state = tmp_path / "lane_state.json"
    _write_config(config, {"A": {"project_url": PROJECT_A}})

    r1 = LaneRouter(config_path=config, state_path=state)
    r1.record_conversation_url("A", CONV_A)

    r2 = LaneRouter(config_path=config, state_path=state)
    assert r2.resolve_target_url("A") == CONV_A

    r2.clear_conversation("A")
    assert r2.resolve_target_url("A") == PROJECT_A
    r3 = LaneRouter(config_path=config, state_path=state)
    assert r3.resolve_target_url("A") == PROJECT_A


def test_record_ignores_unconfigured_peer(tmp_path):
    router = _router(tmp_path, {"A": {"project_url": PROJECT_A}})
    router.record_conversation_url("Z", "https://chatgpt.com/c/x")
    assert router.resolve_target_url("Z") is None


def test_missing_config_is_safe(tmp_path):
    router = LaneRouter(config_path=tmp_path / "nope.json", state_path=tmp_path / "state.json")
    assert router.resolve_target_url("A") is None
    # must not raise and must not create a record
    router.record_conversation_url("A", "https://chatgpt.com/c/x")
    assert router.resolve_target_url("A") is None


# ---- scheduler integration ----

class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.calls = []
        self.reset_calls = []

    async def page_for_lane(self, lane):
        self.calls.append(lane.key)
        return self._page

    async def reset_lane_page(self, lane):
        self.reset_calls.append(lane.key)
        return self._page


class _FakePage:
    def __init__(self, url="https://chatgpt.com/"):
        self._url = url
        self.goto_calls = []

    @property
    def url(self):
        return self._url

    async def goto(self, url, **kwargs):
        self.goto_calls.append(url)
        self._url = url

    async def wait_for_selector(self, selector, **kwargs):
        return object()  # editor is "present"


def test_new_conversation_trigger_acks_and_clears(tmp_path):
    router = _router(tmp_path, {"u1": {"project_url": PROJECT_A}})
    router.record_conversation_url("u1", CONV_A)

    async def ask_func(page, message):  # should not be called
        raise AssertionError("ask_func must not run for a bare /新对话")

    page = _FakePage()
    browser = _FakeBrowser(page)
    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=ask_func, router=router)
    lane = LaneContext.from_metadata({"peer_id": "u1"})

    answer, duration = asyncio.run(scheduler.ask(browser, lane, "/新对话"))

    assert answer == NEW_CONVERSATION_ACK
    assert browser.calls == []  # never opened a page
    assert browser.reset_calls == ["wechat:default:private:u1"]
    # conversation pointer cleared -> next message starts fresh in the project
    assert router.resolve_target_url("u1") == PROJECT_A


def test_scheduler_routes_first_message_then_records(tmp_path):
    router = _router(tmp_path, {"u1": {"project_url": PROJECT_A}})

    async def ask_func(page, message):
        # ChatGPT creates a conversation when you send in a project home
        page._url = CONV_A
        return f"reply:{message}", 0.1

    page = _FakePage("https://chatgpt.com/")
    browser = _FakeBrowser(page)
    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=ask_func, router=router)
    lane = LaneContext.from_metadata({"peer_id": "u1"})

    answer, _ = asyncio.run(scheduler.ask(browser, lane, "你好"))

    assert answer == "reply:你好"
    assert page.goto_calls == [PROJECT_A]  # navigated to project home first
    assert router.resolve_target_url("u1") == CONV_A  # recorded the new conversation


def test_scheduler_resumes_without_extra_navigation(tmp_path):
    router = _router(tmp_path, {"u1": {"project_url": PROJECT_A}})
    router.record_conversation_url("u1", CONV_A)

    async def ask_func(page, message):
        return f"reply:{message}", 0.1

    page = _FakePage(CONV_A)  # already on the saved conversation
    browser = _FakeBrowser(page)
    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=ask_func, router=router)
    lane = LaneContext.from_metadata({"peer_id": "u1"})

    asyncio.run(scheduler.ask(browser, lane, "继续"))

    assert page.goto_calls == []  # no navigation needed


def test_scheduler_force_new_with_payload_navigates_to_project(tmp_path):
    router = _router(tmp_path, {"u1": {"project_url": PROJECT_A}})
    router.record_conversation_url("u1", CONV_A)

    async def ask_func(page, message):
        page._url = CONV_A2  # a brand new conversation
        return f"reply:{message}", 0.1

    page = _FakePage(CONV_A)  # currently on the old conversation
    browser = _FakeBrowser(page)
    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=ask_func, router=router)
    lane = LaneContext.from_metadata({"peer_id": "u1"})

    answer, _ = asyncio.run(scheduler.ask(browser, lane, "/新对话 重新开始"))

    assert answer == "reply:重新开始"
    assert browser.reset_calls == ["wechat:default:private:u1"]
    assert page.goto_calls == [PROJECT_A]  # forced back to project home
    assert router.resolve_target_url("u1") == CONV_A2  # recorded the new conversation


def test_unconfigured_peer_falls_back_without_navigation(tmp_path):
    router = _router(tmp_path, {"u1": {"project_url": PROJECT_A}})

    async def ask_func(page, message):
        return f"reply:{message}", 0.1

    page = _FakePage("https://chatgpt.com/")
    browser = _FakeBrowser(page)
    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=ask_func, router=router)
    lane = LaneContext.from_metadata({"peer_id": "stranger"})

    asyncio.run(scheduler.ask(browser, lane, "你好"))

    assert page.goto_calls == []  # unconfigured -> no routing


def test_unconfigured_new_conversation_does_not_open_default_tab(tmp_path):
    router = _router(tmp_path, {"u1": {"project_url": PROJECT_A}})

    async def ask_func(page, message):
        raise AssertionError("ask_func must not run for a bare /新对话")

    page = _FakePage("https://chatgpt.com/")
    browser = _FakeBrowser(page)
    scheduler = ChatLaneScheduler(max_concurrent_chats=1, ask_func=ask_func, router=router)
    lane = LaneContext.from_metadata({"peer_id": "stranger"})

    answer, _ = asyncio.run(scheduler.ask(browser, lane, "/新对话"))

    assert answer == NEW_CONVERSATION_ACK
    assert browser.calls == []
    assert browser.reset_calls == []
