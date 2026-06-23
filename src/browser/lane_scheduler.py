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
from src.utils.errors import ErrorCode, RelayError

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

# Absolute wall-clock ceiling for a single request (kept just under the bridge's
# 320s so WebDock returns first and frees the slot). This is NOT the soft timeout:
# the soft timeout is an *idle* deadline (give up only if a reply stops
# progressing), so a long but actively-streaming reply keeps going until this cap.
DEFAULT_REQUEST_HARD_CAP_SECONDS = 310.0


def select_chat_timeout(base_seconds: int, with_images_seconds: int, *, has_images: bool) -> int:
    """Image-bearing turns (reference images / image generation) get the longer
    ceiling; text-only turns keep the short one."""
    return with_images_seconds if has_images else base_seconds


@dataclass(frozen=True)
class LaneContext:
    channel: str
    wechat_account: str
    chat_type: str
    peer_id: str
    project: str
    target_url: str | None = None
    previous_target_url: str | None = None

    @property
    def key(self) -> str:
        return build_channel_lane_key(self.channel, self.wechat_account, self.chat_type, self.peer_id)

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None) -> "LaneContext":
        data = metadata or {}
        channel = _safe_channel(data.get("channel") or data.get("platform") or data.get("source"))
        wechat_account = _safe_part(data.get("wechat_account") or data.get("account") or "default")
        chat_type = _safe_part(data.get("chat_type") or "private")
        peer_id = _safe_part(data.get("peer_id") or data.get("open_id") or data.get("chat_id") or data.get("user_id") or "default")
        default_project = "Feishu" if channel == "feishu" else f"WeChat-{wechat_account}"
        project = _safe_project(data.get("chatgpt_project") or data.get("project") or default_project)
        target_url = data.get("chatgpt_conversation_url") or data.get("chatgpt_project_url") or data.get("chatgpt_url")
        previous_target_url = data.get("previous_chatgpt_conversation_url") or data.get("previous_chatgpt_url")
        return cls(
            channel=channel,
            wechat_account=wechat_account,
            chat_type=chat_type,
            peer_id=peer_id,
            project=project,
            target_url=str(target_url).strip() if target_url else None,
            previous_target_url=str(previous_target_url).strip() if previous_target_url else None,
        )


@dataclass(frozen=True)
class ChatResult:
    answer: str
    duration_seconds: float
    lane: LaneContext
    chatgpt_conversation_url: str | None = None

    def __iter__(self):
        yield self.answer
        yield self.duration_seconds

    @property
    def metadata(self) -> dict[str, Any]:
        lane = {
            "key": self.lane.key,
            "channel": self.lane.channel,
            "chat_type": self.lane.chat_type,
            "peer_id": self.lane.peer_id,
            "project": self.lane.project,
            "target_url": self.lane.target_url,
        }
        metadata: dict[str, Any] = {"lane": lane}
        if self.chatgpt_conversation_url:
            metadata["chatgpt_conversation_url"] = self.chatgpt_conversation_url
        return metadata


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
        chat_timeout_seconds: int = 120,
        chat_timeout_seconds_with_images: int = 300,
        request_hard_cap_seconds: float = DEFAULT_REQUEST_HARD_CAP_SECONDS,
    ) -> None:
        self.max_concurrent_chats = max(1, max_concurrent_chats)
        self._chat_timeout_seconds = chat_timeout_seconds
        self._chat_timeout_seconds_with_images = chat_timeout_seconds_with_images
        self._request_hard_cap_seconds = request_hard_cap_seconds
        self._account_semaphore = asyncio.Semaphore(self.max_concurrent_chats)
        self._lane_locks: dict[str, asyncio.Lock] = {}
        self._lane_locks_guard = asyncio.Lock()
        self._media_store = media_store
        self._ask_func = ask_func or self._default_ask
        # Only our default ask accepts the channel arg; injected ask_funcs (tests)
        # keep the legacy (page, message) signature.
        self._ask_func_takes_channel = ask_func is None
        self._router = router or LaneRouter()
        self._image_uploader = image_uploader or upload_images
        self._archiver = archiver or archive_exchange
        self._lane_fallback_window_seconds = lane_fallback_window_seconds
        self._last_active_lane: LaneContext | None = None
        self._last_active_at = 0.0
        # monotonic timestamp of the last request per lane.key, for idle-tab GC.
        self._lane_last_active: dict[str, float] = {}

    async def _default_ask(
        self,
        page: object,
        message: str,
        channel: str = "wechat",
        *,
        timeout_seconds: int | None = None,
        hard_timeout_seconds: float | None = None,
    ) -> tuple[str, float]:
        return await ChatGPTPage(page, media_store=self._media_store, channel=channel).ask(
            message, timeout_seconds=timeout_seconds, hard_timeout_seconds=hard_timeout_seconds
        )

    async def ask(
        self, browser: Any, lane: LaneContext, message: str, images: list[str] | None = None
    ) -> ChatResult:
        force_new, clean_message = parse_new_conversation_trigger(message)
        lane = self._resolve_lane(lane, force_new=force_new)

        lane_lock = await self._get_lane_lock(lane.key)
        async with lane_lock:
            self._lane_last_active[lane.key] = time.monotonic()
            async with self._account_semaphore:
                # "/新对话" with no payload: just drop the saved conversation so the
                # next real message opens a fresh chat in the project. No round-trip.
                reset_page = None
                if force_new:
                    _router_clear_conversation(self._router, lane)
                    if lane.target_url:
                        reset_page = await _reset_lane_page(browser, lane)
                if force_new and not clean_message:
                    await self._archiver(
                        lane, clean_message, images,
                        answer=NEW_CONVERSATION_ACK, duration=0.0, kind="new_conversation",
                    )
                    return ChatResult(NEW_CONVERSATION_ACK, 0.0, lane)

                page = reset_page or await _page_for_lane(browser, lane)
                await self._route_page(page, lane.target_url, force_new=force_new)
                if images:
                    # Attach the inbound image(s) to this lane's composer so the
                    # text that follows edits/answers about them in one turn.
                    await self._image_uploader(page, images)
                effective_timeout = select_chat_timeout(
                    self._chat_timeout_seconds,
                    self._chat_timeout_seconds_with_images,
                    has_images=bool(images),
                )
                # Soft timeout = idle deadline (handled inside the ask). Hard cap =
                # absolute ceiling for an actively-streaming reply; never below the
                # soft timeout. A live reply runs until it finishes or hits this cap
                # — it is NOT cut off at the soft timeout.
                hard_cap = max(effective_timeout, self._request_hard_cap_seconds)
                try:
                    if self._ask_func_takes_channel:
                        coro = self._ask_func(
                            page,
                            clean_message,
                            lane.channel,
                            timeout_seconds=effective_timeout,
                            hard_timeout_seconds=hard_cap,
                        )
                    else:
                        # Injected ask_func uses the legacy (page, message) signature.
                        coro = self._ask_func(page, clean_message)
                    # Hard ceiling on the whole interaction. A stuck browser/CDP op
                    # must release the slot + lane lock (and never outlive the
                    # bridge's 320s timeout) rather than wedge the worker or block
                    # other users' lanes.
                    answer, duration = await asyncio.wait_for(coro, timeout=hard_cap)
                except asyncio.TimeoutError:
                    # Rebuild this lane's tab so the next request starts clean.
                    await _reset_lane_page(browser, lane)
                    exc = RelayError(
                        ErrorCode.RESPONSE_TIMEOUT,
                        f"webdock request exceeded hard cap of {hard_cap:.0f}s; lane reset.",
                    )
                    await self._archiver(lane, clean_message, images, error=exc)
                    raise exc
                except RelayError as exc:
                    # Archive the failed exchange too (with the DOM-snapshot dir
                    # save_debug_dump attached), then let the caller handle it.
                    await self._archiver(lane, clean_message, images, error=exc)
                    raise
                conversation_url = self._record_conversation(lane, page)
                await self._archiver(lane, clean_message, images, answer=answer, duration=duration)
                return ChatResult(answer, duration, lane, conversation_url)

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
        if _router_is_configured(self._router, lane):
            self._last_active_lane = lane
            self._last_active_at = time.monotonic()
        target_url = _router_resolve_target_url(self._router, lane, force_new=force_new)
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

    def _record_conversation(self, lane: LaneContext, page: Any) -> str | None:
        url = _safe_page_url(page)
        if not is_conversation_url(url):
            return None
        try:
            self._router.record_conversation_url(lane.peer_id, url, channel=lane.channel)
        except TypeError:
            self._router.record_conversation_url(lane.peer_id, url)
        return url

    async def status(self) -> dict[str, Any]:
        return {
            "max_concurrent_chats": self.max_concurrent_chats,
            "known_lanes": sorted(self._lane_locks.keys()),
        }

    async def close_idle_lanes(self, browser: Any, idle_seconds: float, *, now: float | None = None) -> list[str]:
        """Close lane tabs idle longer than idle_seconds to bound Chrome memory.

        Skips any lane whose lock is currently held (a request is in flight). For
        the rest we acquire the lock before closing so a tab is never torn down
        mid-request; the next message for that lane just reopens its tab (the
        conversation URL is recorded, login persists). Best-effort per lane."""
        now = time.monotonic() if now is None else now
        closed: list[str] = []
        for key in list(self._lane_locks.keys()):
            lock = self._lane_locks.get(key)
            if lock is None or lock.locked():
                continue
            if now - self._lane_last_active.get(key, 0.0) < idle_seconds:
                continue
            async with lock:
                try:
                    if await _close_lane_page(browser, key):
                        closed.append(key)
                except Exception as exc:  # GC must never break the scheduler
                    log.warning("close idle lane %s failed: %s", key, exc)
                self._lane_last_active.pop(key, None)
        return closed

    async def _get_lane_lock(self, lane_key: str) -> asyncio.Lock:
        async with self._lane_locks_guard:
            lock = self._lane_locks.get(lane_key)
            if lock is None:
                lock = asyncio.Lock()
                self._lane_locks[lane_key] = lock
            return lock


def build_lane_key(wechat_account: str, chat_type: str, peer_id: str) -> str:
    return f"wechat:{_safe_part(wechat_account)}:{_safe_part(chat_type)}:{_safe_part(peer_id)}"


def build_channel_lane_key(channel: str, wechat_account: str, chat_type: str, peer_id: str) -> str:
    if _safe_channel(channel) == "feishu":
        return f"feishu:{_safe_part(peer_id)}"
    return build_lane_key(wechat_account, chat_type, peer_id)


async def _page_for_lane(browser: Any, lane: LaneContext) -> object:
    if hasattr(browser, "page_for_lane"):
        return await browser.page_for_lane(lane)
    return browser.page


async def _reset_lane_page(browser: Any, lane: LaneContext) -> object | None:
    if hasattr(browser, "reset_lane_page"):
        return await browser.reset_lane_page(lane)
    return None


async def _close_lane_page(browser: Any, lane_key: str) -> bool:
    closer = getattr(browser, "close_lane_page", None)
    if closer is None:
        return False
    return bool(await closer(lane_key))


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


def _safe_channel(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"feishu", "lark"}:
        return "feishu"
    return "wechat"


def _safe_project(value: Any) -> str:
    text = str(value or "").strip() or "WeChat-default"
    return text[:128]


def _router_is_configured(router: LaneRouter, lane: LaneContext) -> bool:
    try:
        return router.is_configured(lane.peer_id, channel=lane.channel)
    except TypeError:
        return router.is_configured(lane.peer_id)


def _router_resolve_target_url(router: LaneRouter, lane: LaneContext, *, force_new: bool) -> str | None:
    try:
        return router.resolve_target_url(lane.peer_id, force_new=force_new, channel=lane.channel)
    except TypeError:
        return router.resolve_target_url(lane.peer_id, force_new=force_new)


def _router_clear_conversation(router: LaneRouter, lane: LaneContext) -> None:
    try:
        router.clear_conversation(lane.peer_id, channel=lane.channel)
    except TypeError:
        router.clear_conversation(lane.peer_id)
