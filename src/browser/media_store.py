from __future__ import annotations

from dataclasses import dataclass
import secrets
import time
from threading import Lock


@dataclass(frozen=True)
class MediaItem:
    data: bytes
    content_type: str
    filename: str | None = None


class MediaStore:
    """In-memory store for screenshots/images served at /media/<token>.

    Images are short-lived: OpenClaw downloads the URL right after the reply to
    upload to WeChat, so a small TTL + size cap is plenty and avoids unbounded
    growth. Kept in memory (not disk) since the container restart that would
    clear it also drops any in-flight reply.
    """

    def __init__(self, ttl_seconds: int = 1800, max_items: int = 100) -> None:
        self._ttl = ttl_seconds
        self._max = max_items
        self._lock = Lock()
        self._store: dict[str, tuple[MediaItem, float]] = {}

    def put(self, data: bytes, content_type: str = "image/png", filename: str | None = None) -> str:
        token = secrets.token_urlsafe(16)
        now = time.time()
        with self._lock:
            self._evict(now)
            self._store[token] = (MediaItem(data=data, content_type=content_type, filename=filename), now + self._ttl)
        return token

    def get(self, token: str) -> MediaItem | None:
        now = time.time()
        with self._lock:
            item = self._store.get(token)
            if item is None:
                return None
            media_item, expire_at = item
            if expire_at < now:
                self._store.pop(token, None)
                return None
            return media_item

    def _evict(self, now: float) -> None:
        expired = [token for token, (_, exp) in self._store.items() if exp < now]
        for token in expired:
            self._store.pop(token, None)
        while len(self._store) >= self._max:
            oldest = min(self._store, key=lambda t: self._store[t][1])
            self._store.pop(oldest, None)
