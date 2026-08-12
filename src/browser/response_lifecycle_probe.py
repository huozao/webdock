from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from src.config import Settings, get_settings

log = logging.getLogger(__name__)

_PROBE_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_UUID_PATH_RE = re.compile(
    r"(?i)(?<=/)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/|$)"
)
_TERMINAL_VALUES = frozenset(
    {
        "aborted",
        "cancelled",
        "completed",
        "done",
        "error",
        "failed",
        "finished",
        "finished_successfully",
    }
)
_TERMINAL_SUFFIXES = tuple(f".{value}" for value in _TERMINAL_VALUES)
_BLOCKED_FIELD_NAMES = frozenset(
    {
        "authorization",
        "body",
        "content",
        "cookie",
        "headers",
        "html",
        "message",
        "payload",
        "prompt",
        "query",
        "response_body",
        "text",
        "token",
    }
)
_NETWORK_EVENTS = (
    "Network.requestWillBeSent",
    "Network.responseReceived",
    "Network.dataReceived",
    "Network.loadingFinished",
    "Network.loadingFailed",
    "Network.webSocketCreated",
    "Network.webSocketFrameReceived",
    "Network.webSocketClosed",
)

_request_context: ContextVar[tuple[str, Path] | None] = ContextVar(
    "response_probe_request", default=None
)
_active_context: ContextVar["ResponseLifecycleProbe | None"] = ContextVar(
    "active_response_probe", default=None
)
_probe_claimed = False


def validate_probe_id(value: str | None) -> str | None:
    probe_id = (value or "").strip()
    return probe_id if _PROBE_ID_RE.fullmatch(probe_id) else None


def sanitize_url(value: str | None) -> str:
    try:
        parsed = urlsplit(value or "")
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        return ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return ""
    path = _UUID_PATH_RE.sub(":uuid", parsed.path or "/")
    return f"{parsed.scheme}://{host}{port}{path}"


def diagnostic_probe_enabled(settings: Settings | None = None) -> bool:
    """Read the runtime flag on every submission so Chrome never needs a restart."""
    current = settings or get_settings()
    path = current.browser_profile_dir / "runtime.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return bool(current.diagnostic_probe_enabled)
    value = data.get("diagnostic_probe_enabled") if isinstance(data, dict) else None
    return value if isinstance(value, bool) else bool(current.diagnostic_probe_enabled)


def extract_terminal_markers(raw: str | None) -> list[dict[str, str]]:
    """Extract only allow-listed terminal protocol fields; never return raw data."""
    if not raw:
        return []
    candidates: list[Any] = []
    stripped = raw.strip()
    if stripped:
        try:
            candidates.append(json.loads(stripped))
        except (json.JSONDecodeError, TypeError):
            pass
    for line in raw.splitlines():
        value = line.strip()
        if value.startswith("data:"):
            value = value[5:].strip()
        elif value.startswith("event:"):
            continue
        if value == "[DONE]":
            candidates.append({"sentinel": "DONE"})
            continue
        if not value or value[0] not in "[{":
            continue
        try:
            candidates.append(json.loads(value))
        except json.JSONDecodeError:
            continue

    markers: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered_key = str(key).lower()
                if lowered_key == "sentinel" and child == "DONE":
                    pair = ("sentinel", "DONE")
                    if pair not in seen:
                        seen.add(pair)
                        markers.append({"field": pair[0], "value": pair[1]})
                elif lowered_key in {"event", "state", "status", "type"} and isinstance(child, str):
                    lowered_value = child.strip().lower()
                    if _is_terminal_value(lowered_value):
                        pair = (lowered_key, lowered_value[:128])
                        if pair not in seen:
                            seen.add(pair)
                            markers.append({"field": pair[0], "value": pair[1]})
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for candidate in candidates:
        walk(candidate)
    return markers


def _is_terminal_value(value: str) -> bool:
    return value in _TERMINAL_VALUES or value.endswith(_TERMINAL_SUFFIXES)


@contextmanager
def response_probe_request(probe_id: str | None, output_dir: Path) -> Iterator[None]:
    token = _request_context.set((probe_id, output_dir) if probe_id else None)
    try:
        yield
    finally:
        _request_context.reset(token)


class ResponseLifecycleProbe:
    def __init__(
        self,
        probe_id: str,
        output_dir: Path,
        *,
        max_events: int = 10_000,
        max_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.probe_id = probe_id
        self.output_dir = Path(output_dir)
        self.max_events = max(1, max_events)
        self.max_bytes = max(512, max_bytes)
        self.started_at = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self._encoded_bytes = 0
        self._truncated = False
        self._closed = False
        self._session: Any | None = None
        self._handlers: list[tuple[str, Any]] = []
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._requests: dict[str, dict[str, Any]] = {}
        self._websockets: dict[str, str] = {}
        self._last_dom_signature = ""
        self._capture_task_network = False
        self._context_token: Token[ResponseLifecycleProbe | None] | None = None

    def record(self, event: str, **fields: Any) -> None:
        if self._closed or self._truncated:
            return
        if event == "send_started":
            self._capture_task_network = True
        payload: dict[str, Any] = {
            "offset_ms": round((time.monotonic() - self.started_at) * 1000),
            "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "probe_id": self.probe_id,
            "event": str(event)[:96],
        }
        payload.update(_sanitize_fields(fields))
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(self.events) >= self.max_events or self._encoded_bytes + len(encoded.encode("utf-8")) > self.max_bytes:
            self._append_truncated_event()
            return
        self.events.append(payload)
        self._encoded_bytes += len(encoded.encode("utf-8"))

    async def start(self, page: Any) -> None:
        self._context_token = _active_context.set(self)
        self.record("probe_start")
        context = getattr(page, "context", None)
        if context is None or not hasattr(context, "new_cdp_session"):
            self.record("network_unavailable", reason="page_context_missing")
            return
        self._session = await context.new_cdp_session(page)
        for event in _NETWORK_EVENTS:
            handler = self._make_handler(event)
            self._session.on(event, handler)
            self._handlers.append((event, handler))
        await self._session.send("Network.enable", {})

    async def observe_dom(self, page: Any, **state: Any) -> None:
        try:
            structure = await page.evaluate(_DOM_STRUCTURE_SCRIPT)
        except Exception as exc:
            self.record("dom_observation_failed", error_type=type(exc).__name__)
            return
        safe_state = {**state, "structure": structure if isinstance(structure, dict) else {}}
        signature = json.dumps(safe_state, ensure_ascii=True, sort_keys=True, default=str)
        if signature == self._last_dom_signature:
            return
        self._last_dom_signature = signature
        self.record("dom_state", **safe_state)

    async def close(self, outcome: str) -> None:
        global _probe_claimed
        if self._closed:
            return
        if self._session is not None:
            for event, handler in self._handlers:
                try:
                    self._session.remove_listener(event, handler)
                except Exception:
                    pass
        if self._pending_tasks:
            done, pending = await asyncio.wait(self._pending_tasks, timeout=2)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    task.result()
                except Exception:
                    pass
        self.record("probe_end", outcome=outcome)
        self._closed = True
        if self._session is not None:
            try:
                await self._session.detach()
            except Exception:
                pass
        if self._context_token is not None:
            try:
                _active_context.reset(self._context_token)
            except (ValueError, RuntimeError):
                _active_context.set(None)
        _probe_claimed = False
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            target = self.output_dir / f"{self.probe_id}.jsonl"
            target.write_text(
                "".join(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n" for event in self.events),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("response probe write failed probe_id=%s error=%s", self.probe_id, exc)

    def _append_truncated_event(self) -> None:
        if self._truncated:
            return
        self._truncated = True
        self.events.append(
            {
                "offset_ms": round((time.monotonic() - self.started_at) * 1000),
                "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "probe_id": self.probe_id,
                "event": "probe_truncated",
            }
        )

    def _make_handler(self, event: str):
        def handler(params: dict[str, Any]) -> None:
            try:
                self._handle_network_event(event, params or {})
            except Exception as exc:
                self.record("network_observation_failed", error_type=type(exc).__name__)

        return handler

    def _handle_network_event(self, event: str, params: dict[str, Any]) -> None:
        request_id = str(params.get("requestId") or "")
        if event == "Network.requestWillBeSent":
            request = params.get("request") or {}
            url = sanitize_url(request.get("url"))
            resource_type = str(params.get("type") or "")
            if not _is_task_network_url(url) or resource_type not in {"Fetch", "XHR", "EventSource", "WebSocket"}:
                return
            self._requests[request_id] = {
                "url": url,
                "resource_type": resource_type,
                "bytes": 0,
                "after_send": self._capture_task_network,
            }
            self.record("network_request", request_id=request_id, url=url, resource_type=resource_type)
            return
        if event == "Network.responseReceived" and request_id in self._requests:
            response = params.get("response") or {}
            state = self._requests[request_id]
            state["status"] = response.get("status")
            state["content_type"] = str(response.get("mimeType") or "")[:128]
            self.record(
                "network_response",
                request_id=request_id,
                url=state.get("url"),
                status=state.get("status"),
                content_type=state.get("content_type"),
            )
            return
        if event == "Network.dataReceived" and request_id in self._requests:
            self._requests[request_id]["bytes"] += int(params.get("encodedDataLength") or params.get("dataLength") or 0)
            return
        if event == "Network.loadingFinished" and request_id in self._requests:
            state = self._requests.pop(request_id)
            state["bytes"] = max(state["bytes"], int(params.get("encodedDataLength") or 0))
            self.record("network_finished", request_id=request_id, **state)
            task = asyncio.create_task(self._inspect_response_body(request_id, state))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
            return
        if event == "Network.loadingFailed" and request_id in self._requests:
            state = self._requests.pop(request_id)
            self.record(
                "network_failed",
                request_id=request_id,
                **state,
                cancelled=bool(params.get("canceled")),
                error_type=str(params.get("type") or "")[:64],
            )
            return
        if event == "Network.webSocketCreated":
            url = sanitize_url(params.get("url"))
            if _is_task_network_url(url):
                self._websockets[request_id] = url
                self.record("websocket_opened", request_id=request_id, url=url)
            return
        if event == "Network.webSocketFrameReceived" and request_id in self._websockets:
            payload = (params.get("response") or {}).get("payloadData")
            self._record_protocol_markers(request_id, extract_terminal_markers(payload), "websocket")
            return
        if event == "Network.webSocketFrameReceived" and self._capture_task_network:
            payload = (params.get("response") or {}).get("payloadData")
            self._record_protocol_markers(
                request_id,
                extract_terminal_markers(payload),
                "websocket_unmapped",
            )
            return
        if event == "Network.webSocketClosed" and request_id in self._websockets:
            self.record("websocket_closed", request_id=request_id, url=self._websockets.pop(request_id))

    async def _inspect_response_body(self, request_id: str, state: dict[str, Any]) -> None:
        if self._session is None:
            return
        if not state.get("after_send") or not _is_protocol_candidate_url(str(state.get("url") or "")):
            return
        content_type = str(state.get("content_type") or "").lower()
        if "json" not in content_type and "event-stream" not in content_type:
            return
        try:
            result = await self._session.send("Network.getResponseBody", {"requestId": request_id})
        except Exception:
            return
        markers = extract_terminal_markers(result.get("body") if isinstance(result, dict) else None)
        self._record_protocol_markers(request_id, markers, "response_body")

    def _record_protocol_markers(
        self, request_id: str, markers: list[dict[str, str]], source: str
    ) -> None:
        for marker in markers:
            self.record(
                "protocol_terminal",
                request_id=request_id,
                source=source,
                terminal_field=marker.get("field"),
                terminal_value=marker.get("value"),
            )


async def start_response_probe(page: Any) -> ResponseLifecycleProbe | None:
    global _probe_claimed
    requested = _request_context.get()
    if requested is None or _probe_claimed:
        return None
    probe_id, output_dir = requested
    _probe_claimed = True
    probe = ResponseLifecycleProbe(probe_id, output_dir)
    try:
        await probe.start(page)
    except Exception as exc:
        log.warning("response probe start failed probe_id=%s error=%s", probe_id, exc)
        try:
            await probe.close("probe_error")
        except Exception:
            _probe_claimed = False
        return None
    return probe


async def stop_response_probe(probe: ResponseLifecycleProbe | None, outcome: str) -> None:
    if probe is None:
        return
    try:
        await probe.close(outcome)
    except Exception as exc:
        log.warning("response probe stop failed probe_id=%s error=%s", probe.probe_id, exc)


def record_probe_event(event: str, **fields: Any) -> None:
    probe = _active_context.get()
    if probe is not None:
        probe.record(event, **fields)


async def observe_detector_state(page: Any, **state: Any) -> None:
    probe = _active_context.get()
    if probe is None:
        return
    try:
        await probe.observe_dom(page, **state)
    except Exception as exc:
        probe.record("dom_observation_failed", error_type=type(exc).__name__)


def _is_task_network_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return (
        (host == "chatgpt.com" or host.endswith(".chatgpt.com") or host.endswith(".openai.com"))
        and ("/backend-api/" in path or "/conversation" in path or "/responses" in path)
    )


def _is_protocol_candidate_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path == "/backend-api/f/conversation" or path.endswith("/stream_status")


def _sanitize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in fields.items():
        lowered = str(key).lower()
        if lowered in _BLOCKED_FIELD_NAMES:
            continue
        if "url" in lowered:
            clean_url = sanitize_url(str(value))
            if clean_url:
                sanitized[str(key)[:64]] = clean_url
            continue
        clean = _sanitize_value(value)
        if clean is not None:
            sanitized[str(key)[:64]] = clean
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:64]]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value[:64]]
    if isinstance(value, dict):
        return _sanitize_fields({str(key): child for key, child in list(value.items())[:64]})
    return None


_DOM_STRUCTURE_SCRIPT = r"""
() => {
  const turns = Array.from(document.querySelectorAll("[data-testid^='conversation-turn']"));
  const turn = [...turns].reverse().find((node) => !node.querySelector("[data-message-author-role='user']"));
  if (!turn) return {assistant_turn_present: false, turn_signature: {}, animated_candidates: []};
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  };
  const compact = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: (el.getAttribute("role") || "").slice(0, 64),
    testid: (el.getAttribute("data-testid") || "").slice(0, 96),
    class_tokens: Array.from(el.classList || []).slice(0, 8).map((item) => item.slice(0, 64)),
  });
  const candidates = [];
  for (const el of [turn, ...turn.querySelectorAll("*")]) {
    if (candidates.length >= 32 || !visible(el)) continue;
    const animations = [
      getComputedStyle(el).animationName,
      getComputedStyle(el, "::before").animationName,
      getComputedStyle(el, "::after").animationName,
    ].filter((name) => name && name !== "none");
    if (animations.length) candidates.push({...compact(el), animation_names: [...new Set(animations)].slice(0, 6)});
  }
  return {assistant_turn_present: true, turn_signature: compact(turn), animated_candidates: candidates};
}
"""
