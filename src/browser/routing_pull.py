from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from threading import Event
from typing import Any, Callable
from urllib.request import Request, urlopen

from src.browser.lane_routing import CONFIG_FILENAME, FEISHU_CONFIG_FILENAME, WECOM_CONFIG_FILENAME
from src.config import get_settings

log = logging.getLogger(__name__)

OpenUrl = Callable[[str, float], Any]


def pull_routing_config(url: str, target_path: Path, *, timeout: float = 5.0, opener: OpenUrl | None = None) -> bool:
    open_url = opener or urlopen
    try:
        # Pass timeout as a keyword: the real urllib.request.urlopen takes
        # (url, data=None, timeout=...), so a positional second arg would be sent
        # as the request BODY (data) and raise TypeError, silently never updating.
        request = Request(url, headers={"User-Agent": "WebDock-Routing-Puller/1.0"})
        with open_url(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not _is_valid_config(data):
            raise ValueError("routing config must be an object with lanes")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = target_path.with_name(target_path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target_path)
        return True
    except Exception as exc:  # keep the last known-good routing file
        log.warning("Cannot pull routing config from %s: %s", url, exc)
        return False


def routing_url(base_url: str, channel: str = "wechat") -> str:
    return f"{base_url.rstrip('/')}/v1/routing/{channel}-projects.json"


def pull_default_wechat_routing(backend_base_url: str | None = None, *, timeout: float = 5.0) -> bool:
    base_url = backend_base_url or os.getenv("ALI_ECS_BACKEND_URL") or os.getenv("BACKEND_BASE_URL") or ""
    if not base_url:
        return False
    target = get_settings().browser_profile_dir / CONFIG_FILENAME
    return pull_routing_config(routing_url(base_url, "wechat"), target, timeout=timeout)


class RoutingConfigPuller:
    def __init__(self, backend_base_url: str, target_path: Path, *, channel: str = "wechat", interval_seconds: int = 60):
        self.backend_base_url = backend_base_url
        self.target_path = target_path
        self.channel = channel
        self.interval_seconds = interval_seconds

    def run_once(self) -> bool:
        return pull_routing_config(routing_url(self.backend_base_url, self.channel), self.target_path)

    def run_forever(self, stop_event: Event | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        stop = stop_event or Event()
        while not stop.is_set():
            self.run_once()
            sleep(self.interval_seconds)


def build_pullers(
    backend_base_url: str | None,
    profile_dir: Path,
    *,
    interval_seconds: int = 60,
) -> list[RoutingConfigPuller]:
    """Build one puller per channel, or [] when no backend URL is configured.

    Returning [] (graceful no-op) keeps webdock working standalone when the
    control-plane backend URL is not set in the environment.
    """
    if not backend_base_url:
        return []
    return [
        RoutingConfigPuller(
            backend_base_url, profile_dir / CONFIG_FILENAME,
            channel="wechat", interval_seconds=interval_seconds,
        ),
        RoutingConfigPuller(
            backend_base_url, profile_dir / FEISHU_CONFIG_FILENAME,
            channel="feishu", interval_seconds=interval_seconds,
        ),
        RoutingConfigPuller(
            backend_base_url, profile_dir / WECOM_CONFIG_FILENAME,
            channel="wecom", interval_seconds=interval_seconds,
        ),
    ]


def _is_valid_config(data: Any) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("lanes"), dict):
        return False
    for peer_id, entry in data["lanes"].items():
        if not peer_id or not isinstance(entry, dict) or not entry.get("project_url"):
            return False
    return True
