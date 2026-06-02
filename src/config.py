from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_env: str = "local"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_token: str = ""
    vnc_password: str = "changeme"
    novnc_port: int = 6080
    browser_profile_dir: Path = Path("browser_data")
    debug_dir: Path = Path("logs/debug")
    chatgpt_url: str = "https://chatgpt.com/"
    chat_timeout_seconds: int = 120
    response_stable_seconds: int = 5
    log_level: str = "INFO"
    enable_stealth: bool = True
    slow_mo_ms: int = 50
    typing_delay_min_ms: int = 0
    typing_delay_max_ms: int = 0
    before_type_delay_min_ms: int = 500
    before_type_delay_max_ms: int = 1200
    before_send_delay_min_ms: int = 300
    before_send_delay_max_ms: int = 600
    viewport_width: int = 1366
    viewport_height: int = 768
    viewport_jitter_px: int = 20
    browser_channel: str = "chrome"
    browser_mode: str = "ecs_cdp"
    cdp_url: str = "http://127.0.0.1:9222"
    cdp_connect_timeout_seconds: int = 60
    attach_on_start: bool = False
    max_concurrent_chats: int = 3
    test_media_url: str = ""
    media_base_url: str = ""

    def ensure_dirs(self) -> None:
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    env = _load_env_file(Path(".env"))
    settings = Settings(
        app_env=_get("APP_ENV", "local", env),
        api_host=_get("API_HOST", "0.0.0.0", env),
        api_port=int(_get("API_PORT", "8000", env)),
        api_token=_get("API_TOKEN", "", env),
        vnc_password=_get("VNC_PASSWORD", "changeme", env),
        novnc_port=int(_get("NOVNC_PORT", "6080", env)),
        browser_profile_dir=_path_from_env(_get("BROWSER_PROFILE_DIR", "browser_data", env)),
        debug_dir=_path_from_env(_get("DEBUG_DIR", "logs/debug", env)),
        chatgpt_url=_get("CHATGPT_URL", "https://chatgpt.com/", env),
        chat_timeout_seconds=int(_get("CHAT_TIMEOUT_SECONDS", "120", env)),
        response_stable_seconds=int(_get("RESPONSE_STABLE_SECONDS", "5", env)),
        log_level=_get("LOG_LEVEL", "INFO", env),
        enable_stealth=_get("ENABLE_STEALTH", "true", env).lower() == "true",
        slow_mo_ms=int(_get("SLOW_MO_MS", "50", env)),
        typing_delay_min_ms=int(_get("TYPING_DELAY_MIN_MS", "0", env)),
        typing_delay_max_ms=int(_get("TYPING_DELAY_MAX_MS", "0", env)),
        before_type_delay_min_ms=int(_get("BEFORE_TYPE_DELAY_MIN_MS", "500", env)),
        before_type_delay_max_ms=int(_get("BEFORE_TYPE_DELAY_MAX_MS", "1200", env)),
        before_send_delay_min_ms=int(_get("BEFORE_SEND_DELAY_MIN_MS", "300", env)),
        before_send_delay_max_ms=int(_get("BEFORE_SEND_DELAY_MAX_MS", "600", env)),
        viewport_width=int(_get("VIEWPORT_WIDTH", "1366", env)),
        viewport_height=int(_get("VIEWPORT_HEIGHT", "768", env)),
        viewport_jitter_px=int(_get("VIEWPORT_JITTER_PX", "20", env)),
        browser_channel=_get("BROWSER_CHANNEL", "chrome", env),
        browser_mode=_get("BROWSER_MODE", "ecs_cdp", env).lower(),
        cdp_url=_get("CDP_URL", "http://127.0.0.1:9222", env),
        cdp_connect_timeout_seconds=int(_get("CDP_CONNECT_TIMEOUT_SECONDS", "60", env)),
        attach_on_start=_get("ATTACH_ON_START", "false", env).lower() == "true",
        max_concurrent_chats=int(_get("MAX_CONCURRENT_CHATS", "3", env)),
        media_base_url=_get("MEDIA_BASE_URL", "", env),
    )
    settings.ensure_dirs()
    return _apply_runtime_overrides(settings)


def _get(name: str, default: str, env_file_values: dict[str, str]) -> str:
    return os.getenv(name) or env_file_values.get(name) or default


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _path_from_env(value: str) -> Path:
    if os.name == "nt":
        normalized = value.replace("\\", "/")
        if normalized == "/app/browser_data":
            return Path("browser_data")
        if normalized == "/app/logs/debug":
            return Path("logs/debug")
    return Path(value)


# Runtime overrides: tune a few hot values (chat timeout) WITHOUT recreating the
# container. Edit browser_data/runtime.json and restart only the api process —
# this avoids restarting Chrome, which would break the warmed-up ChatGPT login
# session (project red line). Missing/corrupt file degrades to env/defaults.
_RUNTIME_OVERRIDE_INT_FIELDS = ("chat_timeout_seconds", "response_stable_seconds")
_RUNTIME_OVERRIDE_STR_FIELDS = ("test_media_url", "media_base_url")


def _apply_runtime_overrides(settings: Settings) -> Settings:
    path = settings.browser_profile_dir / "runtime.json"
    try:
        if not path.exists():
            return settings
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return settings
    if not isinstance(data, dict):
        return settings
    overrides: dict[str, object] = {}
    for field in _RUNTIME_OVERRIDE_INT_FIELDS:
        if field in data:
            try:
                overrides[field] = int(data[field])
            except (TypeError, ValueError):
                continue
    for field in _RUNTIME_OVERRIDE_STR_FIELDS:
        value = data.get(field)
        if isinstance(value, str):
            overrides[field] = value
    return replace(settings, **overrides) if overrides else settings
