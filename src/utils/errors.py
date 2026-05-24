from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    NOT_LOGGED_IN = "NOT_LOGGED_IN"
    CHAT_INPUT_NOT_FOUND = "CHAT_INPUT_NOT_FOUND"
    SEND_BUTTON_NOT_FOUND = "SEND_BUTTON_NOT_FOUND"
    RESPONSE_TIMEOUT = "RESPONSE_TIMEOUT"
    RESPONSE_EMPTY = "RESPONSE_EMPTY"
    SELECTOR_FAILED = "SELECTOR_FAILED"
    BROWSER_NOT_STARTED = "BROWSER_NOT_STARTED"
    BUSY = "BUSY"
    AUTH_FAILED = "AUTH_FAILED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class RelayError(Exception):
    def __init__(self, code: ErrorCode, message: str, debug_dir: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.debug_dir = debug_dir


def error_response(code: ErrorCode, message: str, debug_dir: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error_code": code.value,
        "message": message,
    }
    if debug_dir:
        payload["debug_dir"] = debug_dir
    return payload
