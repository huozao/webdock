from __future__ import annotations

import time
import uuid
import json
from typing import Any

from pydantic import BaseModel, Field


class BrowserStatusResponse(BaseModel):
    ok: bool = True
    browser_started: bool
    chrome_running: bool = False
    cdp_attached: bool = False
    chrome_version: str | None = None
    current_url: str | None = None
    chat_input_found: bool = False
    send_button_found: bool = False
    assistant_message_found: bool = False
    cloudflare_challenge_detected: bool = False
    auth_error_detected: bool = False
    login_status: str = "unknown"
    last_error: str | None = None


class ChatSuccessResponse(BaseModel):
    ok: bool = True
    answer: str
    duration_seconds: float


class OpenAIChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class OpenAIChoice(BaseModel):
    index: int = 0
    message: OpenAIChoiceMessage
    finish_reason: str = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:24]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "browser-chatgpt"
    choices: list[OpenAIChoice]
    usage: OpenAIUsage = Field(default_factory=OpenAIUsage)


def build_openai_response(
    model: str,
    answer: str,
    prompt: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(answer) // 4)
    response = OpenAIChatCompletionResponse(
        model=model,
        choices=[OpenAIChoice(message=OpenAIChoiceMessage(content=answer))],
        usage=OpenAIUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )
    payload = response.model_dump()
    if metadata:
        payload["metadata"] = metadata
    return payload


def build_openai_models_response() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "browser-chatgpt",
                "object": "model",
                "created": 0,
                "owned_by": "webdock",
            }
        ],
    }


def build_openai_sse_events(model: str, answer: str) -> list[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    content_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}],
    }
    stop_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return [
        f"data: {json.dumps(content_chunk, ensure_ascii=False)}\n\n",
        f"data: {json.dumps(stop_chunk, ensure_ascii=False)}\n\n",
        "data: [DONE]\n\n",
    ]
