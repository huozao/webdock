from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.browser.image_input import extract_image_urls
from src.browser.lane_scheduler import LaneContext
from src.models.request_models import ChatRequest, OpenAIChatCompletionRequest
from src.models.response_models import build_openai_models_response, build_openai_response, build_openai_sse_events
from src.utils.errors import ErrorCode, RelayError, error_response

router = APIRouter()

_OPENCLAW_METADATA_PREFIX_RE = re.compile(
    r"\A(?:\[[^\]\n]*UTC\]\s*)?Conversation info \(untrusted metadata\):\s*",
    flags=re.DOTALL,
)


@router.post("/chat")
async def chat(request: Request, body: ChatRequest) -> JSONResponse:
    result = await _ask_browser(request, body.message, LaneContext.from_metadata(None))
    if isinstance(result, JSONResponse):
        return result
    answer, duration = result
    payload: dict[str, Any] = {"ok": True, "answer": answer, "duration_seconds": duration}
    metadata = getattr(result, "metadata", None)
    if metadata:
        payload["metadata"] = metadata
    return JSONResponse(content=payload)


@router.get("/v1/models")
async def openai_models() -> dict[str, Any]:
    return build_openai_models_response()


@router.post("/v1/chat/completions", response_model=None)
async def openai_chat_completions(
    request: Request, body: OpenAIChatCompletionRequest
) -> dict[str, Any] | StreamingResponse:
    if body.tools or body.tool_choice:
        raise HTTPException(
            status_code=400,
            detail=error_response(
                ErrorCode.SELECTOR_FAILED,
                "webdock does not support tools/tool_choice. Disable tools for this provider.",
            ),
        )

    messages = [msg.model_dump() for msg in body.messages]
    prompt = build_prompt_from_messages(messages)
    images = extract_images_from_messages(messages)
    if not prompt.strip() and not images:
        raise HTTPException(
            status_code=400,
            detail=error_response(ErrorCode.RESPONSE_EMPTY, "No text user message found in messages."),
        )

    result = await _ask_browser(request, prompt, LaneContext.from_metadata(body.metadata), images=images)
    if isinstance(result, JSONResponse):
        raise HTTPException(status_code=result.status_code, detail=json.loads(result.body))

    answer, _duration = result
    metadata = getattr(result, "metadata", None)
    if body.stream:
        return StreamingResponse(
            iter(build_openai_sse_events(body.model, answer)),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )
    return build_openai_response(body.model, answer, prompt, metadata=metadata)


def build_prompt_from_messages(messages: list[dict[str, Any]]) -> str:
    system_parts: list[str] = []
    user_parts: list[str] = []

    for message in messages:
        role = message.get("role")
        content = _content_to_text(message.get("content"))
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            user_parts.append(content)

    if not user_parts:
        return ""

    prefix = "\n\n".join(system_parts).strip()
    user_text = clean_openclaw_metadata(user_parts[-1]).strip()
    return f"{prefix}\n\n{user_text}".strip() if prefix else user_text


def clean_openclaw_metadata(text: str) -> str:
    if not isinstance(text, str):
        return ""
    metadata_end = _openclaw_metadata_prefix_end(text)
    return (text[metadata_end:] if metadata_end else text).strip()


def _openclaw_metadata_prefix_end(text: str) -> int:
    match = _OPENCLAW_METADATA_PREFIX_RE.match(text)
    if not match:
        return 0

    pos = match.end()
    fence = re.match(r"```json\s*", text[pos:], flags=re.IGNORECASE)
    if fence:
        payload_start = pos + fence.end()
        payload_end = text.find("```", payload_start)
        if payload_end < 0:
            return 0
        end = payload_end + 3
    else:
        language = re.match(r"json(?:\s+|$)", text[pos:], flags=re.IGNORECASE)
        if language:
            pos += language.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
        try:
            _payload, end = json.JSONDecoder().raw_decode(text, pos)
        except json.JSONDecodeError:
            return 0

    while end < len(text) and text[end].isspace():
        end += 1
    return end


def extract_images_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    """Image URLs (data: or http(s)) carried by the latest user message, in
    OpenAI vision format. Empty when the message is plain text."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return extract_image_urls(message.get("content"))
    return []


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        # Pull the text parts; image parts are handled separately (uploaded to
        # ChatGPT), so they are skipped here rather than rejected.
        parts = [
            str(item.get("text", "")).strip()
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return str(content).strip()


async def _ask_browser(
    request: Request, message: str, lane: LaneContext, images: list[str] | None = None
) -> Any | JSONResponse:
    browser = request.app.state.browser
    attach_error = await _ensure_browser_ready(browser)
    if attach_error:
        return attach_error

    scheduler = request.app.state.chat_scheduler
    try:
        return await scheduler.ask(browser, lane, message, images=images)
    except RelayError as exc:
        browser.last_error = f"{exc.code.value}: {exc.message}"
        status_code = _status_code_for_error(exc.code)
        return JSONResponse(
            status_code=status_code,
            content=error_response(exc.code, exc.message, exc.debug_dir),
        )


async def _ensure_browser_ready(browser: Any) -> JSONResponse | None:
    if browser.started and browser.page is not None:
        return None

    try:
        await browser.start()
    except Exception as exc:
        browser.last_error = str(exc)
        return JSONResponse(
            status_code=503,
            content=error_response(
                ErrorCode.BROWSER_NOT_STARTED,
                f"Chrome not running or CDP attach failed: {exc}. Make sure Chrome is running in noVNC, then retry.",
            ),
        )

    if not browser.started or browser.page is None:
        return JSONResponse(
            status_code=503,
            content=error_response(
                ErrorCode.BROWSER_NOT_STARTED,
                "Browser attach did not produce a page. Make sure Chrome is running in noVNC, then retry.",
            ),
        )
    browser.last_error = None
    return None


def _status_code_for_error(code: ErrorCode) -> int:
    if code == ErrorCode.BUSY:
        return 429
    if code in {ErrorCode.NOT_LOGGED_IN, ErrorCode.AUTH_FAILED}:
        return 401
    if code == ErrorCode.BROWSER_NOT_STARTED:
        return 503
    return 500
