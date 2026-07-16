from __future__ import annotations

import json

from src.browser.routing_pull import pull_routing_config


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_pull_routing_config_success_overwrites_local_file(tmp_path):
    target = tmp_path / "wechat_projects.json"
    target.write_text(json.dumps({"lanes": {"old": {"project_url": "old"}}}), encoding="utf-8")

    ok = pull_routing_config(
        "https://aliecs.example/v1/routing/wechat-projects.json",
        target,
        opener=lambda url, timeout: FakeResponse({"lanes": {"wxid_a": {"name": "张三", "project_url": "p1"}}}),
    )

    assert ok is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"lanes": {"wxid_a": {"name": "张三", "project_url": "p1"}}}


def test_pull_routing_config_calls_opener_like_urlopen(tmp_path):
    """Regression: the opener must be called the way urllib.request.urlopen is
    (url, data=None, timeout=...). A positional second arg lands in `data` (the
    request body) and makes the real urlopen raise TypeError, so the routing file
    silently never updates from the backend."""
    target = tmp_path / "wechat_projects.json"
    target.write_text(json.dumps({"lanes": {"old": {"project_url": "old"}}}), encoding="utf-8")
    captured: dict = {}

    def urlopen_like(url, data=None, timeout=None):  # mirrors urllib.request.urlopen
        if data is not None:
            raise TypeError("message_body should be a bytes-like object or an iterable")
        captured["timeout"] = timeout
        return FakeResponse({"lanes": {"wxid_a": {"project_url": "p1"}}})

    ok = pull_routing_config(
        "https://aliecs.example/v1/routing/wechat-projects.json",
        target,
        timeout=7.0,
        opener=urlopen_like,
    )

    assert ok is True
    assert captured["timeout"] == 7.0
    assert json.loads(target.read_text(encoding="utf-8")) == {"lanes": {"wxid_a": {"project_url": "p1"}}}


def test_pull_routing_config_failure_keeps_old_file(tmp_path):
    target = tmp_path / "wechat_projects.json"
    original = {"lanes": {"old": {"project_url": "old"}}}
    target.write_text(json.dumps(original), encoding="utf-8")

    def boom(url, timeout):
        raise TimeoutError("backend down")

    ok = pull_routing_config("https://aliecs.example/v1/routing/wechat-projects.json", target, opener=boom)

    assert ok is False
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_build_pullers_empty_when_no_url(tmp_path):
    from src.browser.routing_pull import build_pullers

    assert build_pullers(None, tmp_path) == []
    assert build_pullers("", tmp_path) == []


def test_build_pullers_returns_wechat_feishu_and_wecom(tmp_path):
    from src.browser.routing_pull import build_pullers

    pullers = build_pullers("https://aliecs.example/api", tmp_path, interval_seconds=30)

    assert {p.channel for p in pullers} == {"wechat", "feishu", "wecom"}
    assert {p.target_path.name for p in pullers} == {
        "wechat_projects.json",
        "feishu_projects.json",
        "wecom_projects.json",
    }
    assert all(p.interval_seconds == 30 for p in pullers)
    assert all(p.backend_base_url == "https://aliecs.example/api" for p in pullers)
