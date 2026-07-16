from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any

from src.config import get_settings

log = logging.getLogger(__name__)

# Microsoft message that opens a fresh conversation inside the lane's project.
NEW_CONVERSATION_TRIGGER = "/新对话"
NEW_CONVERSATION_ACK = "✅ 已为你开启新对话，请发送你的问题。"

CONFIG_FILENAME = "wechat_projects.json"
FEISHU_CONFIG_FILENAME = "feishu_projects.json"
WECOM_CONFIG_FILENAME = "wecom_projects.json"
STATE_FILENAME = "lane_state.json"


def parse_new_conversation_trigger(message: str) -> tuple[bool, str]:
    """Detect the '/新对话' trigger at the start of a message.

    Returns (force_new, message_without_trigger). A trigger must be the whole
    message or be followed by whitespace, so '/新对话题' is NOT treated as a
    trigger. Assumes the incoming text is the raw user message (the OpenClaw
    bridge forwards only the user turn, no system prompt prefix).
    """
    if not isinstance(message, str):
        return False, message
    stripped = message.strip()
    if stripped == NEW_CONVERSATION_TRIGGER:
        return True, ""
    if stripped.startswith(NEW_CONVERSATION_TRIGGER):
        rest = stripped[len(NEW_CONVERSATION_TRIGGER):]
        if rest == "" or rest[0].isspace():
            return True, rest.strip()
    return False, message


def is_conversation_url(url: str | None) -> bool:
    """True for a concrete ChatGPT conversation URL (.../c/<id>), not a project home."""
    return bool(url) and "chatgpt.com" in url and "/c/" in url


class LaneRouter:
    """Maps a WeChat peer_id to its ChatGPT project, and remembers the live
    conversation URL per peer so messages keep landing in the same chat.

    - config (read-only, user-maintained): wechat_projects.json
        {"lanes": {"<peer_id>": {"name": "...", "project_url": ".../project"}}}
    - state (read-write, owned by webdock): lane_state.json
        {"<peer_id>": {"conversation_url": ".../c/<id>"}}

    Both live in browser_profile_dir (the persistent volume). Missing/corrupt
    files degrade gracefully to "no routing" (fallback to current behaviour).
    """

    def __init__(
        self,
        config_path: Path | None = None,
        state_path: Path | None = None,
        feishu_config_path: Path | None = None,
        wecom_config_path: Path | None = None,
    ) -> None:
        if config_path is None or state_path is None:
            base = get_settings().browser_profile_dir
        else:
            base = Path(config_path).parent
        self._config_path = Path(config_path) if config_path else base / CONFIG_FILENAME
        self._feishu_config_path = Path(feishu_config_path) if feishu_config_path else base / FEISHU_CONFIG_FILENAME
        self._wecom_config_path = Path(wecom_config_path) if wecom_config_path else base / WECOM_CONFIG_FILENAME
        self._state_path = Path(state_path) if state_path else base / STATE_FILENAME
        self._lock = Lock()
        self._config_paths = {
            "wechat": self._config_path,
            "feishu": self._feishu_config_path,
            "wecom": self._wecom_config_path,
        }
        self._configs = {
            "wechat": self._load_config(self._config_path),
            "feishu": self._load_config(self._feishu_config_path),
            "wecom": self._load_config(self._wecom_config_path),
        }
        self._config_mtimes = {ch: _mtime(p) for ch, p in self._config_paths.items()}
        self._config = self._configs["wechat"]
        self._state = self._load_state()

    # ---- public API ----
    def resolve_target_url(self, peer_id: str | None, *, force_new: bool = False, channel: str = "wechat") -> str | None:
        """The URL this lane should be on. None => not configured => fallback."""
        if not peer_id:
            return None
        normalized = _normalize_channel(channel)
        config = self._config_for(normalized)
        entry = config.get(peer_id) or config.get("*") or {}
        project_url = entry.get("project_url")
        conversation_url = (self._state.get(_state_key(normalized, peer_id)) or {}).get("conversation_url")
        if force_new:
            return project_url
        return conversation_url or project_url

    def record_conversation_url(self, peer_id: str | None, url: str | None, *, channel: str = "wechat") -> None:
        """Remember the live conversation URL for a configured peer."""
        normalized = _normalize_channel(channel)
        if not peer_id or not url:
            return
        # WeCom peers are discovered dynamically from the official channel
        # plugin. Persist their conversation URLs even before the control-plane
        # has a managed_contacts row for the user/group.
        if normalized != "wecom" and peer_id not in self._config_for(normalized):
            return
        with self._lock:
            entry = self._state.setdefault(_state_key(normalized, peer_id), {})
            if entry.get("conversation_url") == url:
                return
            entry["conversation_url"] = url
            self._save_state_locked()

    def clear_conversation(self, peer_id: str | None, *, channel: str = "wechat") -> None:
        """Forget the live conversation so the next message starts a new one."""
        if not peer_id:
            return
        with self._lock:
            entry = self._state.get(_state_key(channel, peer_id))
            if entry and "conversation_url" in entry:
                entry.pop("conversation_url", None)
                self._save_state_locked()

    def is_configured(self, peer_id: str | None, *, channel: str = "wechat") -> bool:
        if not peer_id:
            return False
        normalized = _normalize_channel(channel)
        return normalized == "wecom" or peer_id in self._config_for(normalized)

    def lane_name(self, peer_id: str | None, *, channel: str = "wechat") -> str:
        config = self._config_for(channel)
        entry = config.get(peer_id or "") or config.get("*")
        return (entry or {}).get("name") or (peer_id or "")

    # ---- internal ----
    def _load_config(self, path: Path) -> dict[str, dict[str, Any]]:
        data = _read_json(path)
        lanes = data.get("lanes") if isinstance(data, dict) else None
        if not isinstance(lanes, dict):
            return {}
        config: dict[str, dict[str, Any]] = {}
        for peer_id, entry in lanes.items():
            if isinstance(entry, dict) and entry.get("project_url"):
                config[str(peer_id)] = entry
        return config

    def _config_for(self, channel: str) -> dict[str, dict[str, Any]]:
        normalized = _normalize_channel(channel)
        self._maybe_reload(normalized)
        return self._configs.get(normalized, self._configs["wechat"])

    def _maybe_reload(self, channel: str) -> None:
        """Reload a channel's config from disk when its file changed. The routing
        puller rewrites wechat_projects.json / feishu_projects.json every ~60s from
        the control-plane sheet, so a long-running webdock must pick up project_url
        changes without a restart (config is otherwise only read at __init__)."""
        path = self._config_paths.get(channel)
        if path is None:
            return
        mtime = _mtime(path)
        if mtime == self._config_mtimes.get(channel):
            return
        with self._lock:
            if mtime == self._config_mtimes.get(channel):
                return
            self._configs[channel] = self._load_config(path)
            self._config_mtimes[channel] = mtime
            if channel == "wechat":
                self._config = self._configs["wechat"]

    def _load_state(self) -> dict[str, dict[str, Any]]:
        data = _read_json(self._state_path)
        return data if isinstance(data, dict) else {}

    def _save_state_locked(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_name(self._state_path.name + ".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except Exception as exc:  # never let state IO break a chat
            log.warning("Cannot save lane state %s: %s", self._state_path, exc)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_json(path: Path) -> Any:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Cannot read JSON %s: %s", path, exc)
        return {}


def _normalize_channel(channel: str | None) -> str:
    text = str(channel or "").strip().lower()
    if text in {"feishu", "lark"}:
        return "feishu"
    if text in {"wecom", "qywx", "wework", "enterprise-wechat"}:
        return "wecom"
    return "wechat"


def _state_key(channel: str, peer_id: str) -> str:
    normalized = _normalize_channel(channel)
    return peer_id if normalized == "wechat" else f"{normalized}:{peer_id}"
