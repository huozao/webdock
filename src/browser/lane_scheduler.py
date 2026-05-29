from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.browser.chatgpt_page import ChatGPTPage

AskFunc = Callable[[object, str], Awaitable[tuple[str, float]]]

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


@dataclass(frozen=True)
class LaneContext:
    wechat_account: str
    chat_type: str
    peer_id: str
    project: str
    target_url: str | None = None

    @property
    def key(self) -> str:
        return build_lane_key(self.wechat_account, self.chat_type, self.peer_id)

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None) -> "LaneContext":
        data = metadata or {}
        wechat_account = _safe_part(data.get("wechat_account") or data.get("account") or "default")
        chat_type = _safe_part(data.get("chat_type") or "private")
        peer_id = _safe_part(data.get("peer_id") or data.get("chat_id") or data.get("user_id") or "default")
        project = _safe_project(data.get("chatgpt_project") or data.get("project") or f"WeChat-{wechat_account}")
        target_url = data.get("chatgpt_conversation_url") or data.get("chatgpt_project_url") or data.get("chatgpt_url")
        return cls(
            wechat_account=wechat_account,
            chat_type=chat_type,
            peer_id=peer_id,
            project=project,
            target_url=str(target_url).strip() if target_url else None,
        )


class ChatLaneScheduler:
    def __init__(
        self,
        *,
        max_concurrent_chats: int,
        ask_func: AskFunc | None = None,
    ) -> None:
        self.max_concurrent_chats = max(1, max_concurrent_chats)
        self._account_semaphore = asyncio.Semaphore(self.max_concurrent_chats)
        self._lane_locks: dict[str, asyncio.Lock] = {}
        self._lane_locks_guard = asyncio.Lock()
        self._ask_func = ask_func or _ask_chatgpt_page

    async def ask(self, browser: Any, lane: LaneContext, message: str) -> tuple[str, float]:
        lane_lock = await self._get_lane_lock(lane.key)
        async with lane_lock:
            async with self._account_semaphore:
                page = await _page_for_lane(browser, lane)
                return await self._ask_func(page, message)

    async def status(self) -> dict[str, Any]:
        return {
            "max_concurrent_chats": self.max_concurrent_chats,
            "known_lanes": sorted(self._lane_locks.keys()),
        }

    async def _get_lane_lock(self, lane_key: str) -> asyncio.Lock:
        async with self._lane_locks_guard:
            lock = self._lane_locks.get(lane_key)
            if lock is None:
                lock = asyncio.Lock()
                self._lane_locks[lane_key] = lock
            return lock


def build_lane_key(wechat_account: str, chat_type: str, peer_id: str) -> str:
    return f"wechat:{_safe_part(wechat_account)}:{_safe_part(chat_type)}:{_safe_part(peer_id)}"


async def _page_for_lane(browser: Any, lane: LaneContext) -> object:
    if hasattr(browser, "page_for_lane"):
        return await browser.page_for_lane(lane)
    return browser.page


async def _ask_chatgpt_page(page: object, message: str) -> tuple[str, float]:
    return await ChatGPTPage(page).ask(message)


def _safe_part(value: Any) -> str:
    text = str(value or "").strip() or "default"
    return _SAFE_KEY_RE.sub("_", text)[:96]


def _safe_project(value: Any) -> str:
    text = str(value or "").strip() or "WeChat-default"
    return text[:128]
