from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.browser import selectors
from src.browser.chatgpt_page import ChatGPTPage
from src.browser.detector import find_first
from src.browser.lane_routing import (
    LaneRouter,
    NEW_CONVERSATION_ACK,
    is_conversation_url,
    parse_new_conversation_trigger,
)

log = logging.getLogger(__name__)

AskFunc = Callable[[object, str], Awaitable[tuple[str, float]]]

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.:-]+")

# After navigating to a project/conversation URL, wait this long for the editor.
ROUTE_INPUT_TIMEOUT_MS = 10000


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
        router: LaneRouter | None = None,
        media_store: Any | None = None,
    ) -> None:
        self.max_concurrent_chats = max(1, max_concurrent_chats)
        self._account_semaphore = asyncio.Semaphore(self.max_concurrent_chats)
        self._lane_locks: dict[str, asyncio.Lock] = {}
        self._lane_locks_guard = asyncio.Lock()
        self._media_store = media_store
        self._ask_func = ask_func or self._default_ask
        self._router = router or LaneRouter()

    async def _default_ask(self, page: object, message: str) -> tuple[str, float]:
        return await ChatGPTPage(page, media_store=self._media_store).ask(message)

    async def ask(self, browser: Any, lane: LaneContext, message: str) -> tuple[str, float]:
        force_new, clean_message = parse_new_conversation_trigger(message)
        target_url = self._router.resolve_target_url(lane.peer_id, force_new=force_new)
        if target_url and target_url != lane.target_url:
            lane = dataclasses.replace(lane, target_url=target_url)

        lane_lock = await self._get_lane_lock(lane.key)
        async with lane_lock:
            async with self._account_semaphore:
                # "/新对话" with no payload: just drop the saved conversation so the
                # next real message opens a fresh chat in the project. No round-trip.
                if force_new and not clean_message:
                    self._router.clear_conversation(lane.peer_id)
                    return NEW_CONVERSATION_ACK, 0.0

                page = await _page_for_lane(browser, lane)
                await self._route_page(page, lane.target_url, force_new=force_new)
                answer, duration = await self._ask_func(page, clean_message)
                self._record_conversation(lane.peer_id, page)
                return answer, duration

    async def _route_page(self, page: Any, target_url: str | None, *, force_new: bool) -> None:
        """Make sure the lane's page is on its project/conversation URL.

        No-op when the lane is unconfigured (target_url is None) -> fallback to
        whatever page the browser manager produced (current behaviour).
        """
        if not target_url:
            return
        current = _safe_page_url(page)
        if not (force_new or current != target_url):
            return
        try:
            await page.goto(target_url, wait_until="domcontentloaded")
            await find_first(page, selectors.CHAT_INPUT, visible=True, timeout_ms=ROUTE_INPUT_TIMEOUT_MS)
        except Exception as exc:  # navigation failure must not block the chat
            log.warning("Lane routing navigation to %s failed: %s", target_url, exc)

    def _record_conversation(self, peer_id: str, page: Any) -> None:
        url = _safe_page_url(page)
        if is_conversation_url(url):
            self._router.record_conversation_url(peer_id, url)

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


def _safe_page_url(page: Any) -> str | None:
    try:
        url = page.url
        return url() if callable(url) else url
    except Exception:
        return None


def _safe_part(value: Any) -> str:
    text = str(value or "").strip() or "default"
    return _SAFE_KEY_RE.sub("_", text)[:96]


def _safe_project(value: Any) -> str:
    text = str(value or "").strip() or "WeChat-default"
    return text[:128]
