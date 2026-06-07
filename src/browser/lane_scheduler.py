from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.browser import selectors
from src.browser.chatgpt_page import ChatGPTPage, upload_images
from src.browser.detector import find_first
from src.browser.lane_routing import (
    LaneRouter,
    NEW_CONVERSATION_ACK,
    is_conversation_url,
    parse_new_conversation_trigger,
)
from src.browser.message_archive import archive_exchange
from src.utils.errors import RelayError

log = logging.getLogger(__name__)

AskFunc = Callable[[object, str], Awaitable[tuple[str, float]]]

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.:-]+")

# After navigating to a project/conversation URL, wait this long for the editor.
ROUTE_INPUT_TIMEOUT_MS = 10000

# Sentinel peer_id produced by LaneContext.from_metadata when a request carries
# no OpenClaw routing metadata (see _safe_part).
DEFAULT_PEER = "default"
# A WeChat "text + images" send arrives as several separate requests and only the
# text one carries metadata; the image requests reach us metadata-less. For this
# long after a configured lane was last seen, a metadata-less request inherits it
# so those images land in the same conversation instead of a stray default chat.
LANE_FALLBACK_WINDOW_SECONDS = 120.0


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
        lane_fallback_window_seconds: float = LANE_FALLBACK_WINDOW_SECONDS,
        image_uploader: Callable[[Any, list[str]], Awaitable[int]] | None = None,
        archiver: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.max_concurrent_chats = max(1, max_concurrent_chats)
        self._account_semaphore = asyncio.Semaphore(self.max_concurrent_chats)
        self._lane_locks: dict[str, asyncio.Lock] = {}
        self._lane_locks_guard = asyncio.Lock()
        self._media_store = media_store
        self._ask_func = ask_func or self._default_ask
        self._router = router or LaneRouter()
        self._image_uploader = image_uploader or upload_images
        self._archiver = archiver or archive_exchange
        self._lane_fallback_window_seconds = lane_fallback_window_seconds
        self._last_active_lane: LaneContext | None = None
        self._last_active_at = 0.0

    async def _default_ask(self, page: object, message: str) -> tuple[str, float]:
        return await ChatGPTPage(page, media_store=self._media_store).ask(message)

    async def ask(
        self, browser: Any, lane: LaneContext, message: str, images: list[str] | None = None
    ) -> tuple[str, float]:
        force_new, clean_message = parse_new_conversation_trigger(message)
        lane = self._resolve_lane(lane, force_new=force_new)

        lane_lock = await self._get_lane_lock(lane.key)
        async with lane_lock:
            async with self._account_semaphore:
                # "/新对话" with no payload: just drop the saved conversation so the
                # next real message opens a fresh chat in the project. No round-trip.
                if force_new and not clean_message:
                    self._router.clear_conversation(lane.peer_id)
                    await self._archiver(
                        lane, clean_message, images,
                        answer=NEW_CONVERSATION_ACK, duration=0.0, kind="new_conversation",
                    )
                    return NEW_CONVERSATION_ACK, 0.0

                page = await _page_for_lane(browser, lane)
                await self._route_page(page, lane.target_url, force_new=force_new)
                if images:
                    # Attach the inbound image(s) to this lane's composer so the
                    # text that follows edits/answers about them in one turn.
                    await self._image_uploader(page, images)
                try:
                    answer, duration = await self._ask_func(page, clean_message)
                except RelayError as exc:
                    # Archive the failed exchange too (with the DOM-snapshot dir
                    # save_debug_dump attached), then let the caller handle it.
                    await self._archiver(lane, clean_message, images, error=exc)
                    raise
                self._record_conversation(lane.peer_id, page)
                await self._archiver(lane, clean_message, images, answer=answer, duration=duration)
                return answer, duration

    def _resolve_lane(self, lane: LaneContext, *, force_new: bool) -> LaneContext:
        """Pick the lane this request really belongs to and attach its target URL.

        A metadata-less request (default sentinel peer_id) inherits the most
        recent configured lane within the fallback window, so a WeChat image that
        arrived as its own request right after the text lands in the same
        conversation rather than opening a stray default chat. '/新对话'
        (force_new) is never inherited — it always means "this exact lane".
        """
        if not force_new and lane.peer_id == DEFAULT_PEER:
            inherited = self._recent_active_lane()
            if inherited is not None:
                lane = inherited
        if self._router.is_configured(lane.peer_id):
            self._last_active_lane = lane
            self._last_active_at = time.monotonic()
        target_url = self._router.resolve_target_url(lane.peer_id, force_new=force_new)
        if target_url and target_url != lane.target_url:
            lane = dataclasses.replace(lane, target_url=target_url)
        return lane

    def _recent_active_lane(self) -> LaneContext | None:
        lane = self._last_active_lane
        if lane is None:
            return None
        if time.monotonic() - self._last_active_at > self._lane_fallback_window_seconds:
            return None
        return lane

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
