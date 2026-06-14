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


def test_pull_routing_config_failure_keeps_old_file(tmp_path):
    target = tmp_path / "wechat_projects.json"
    original = {"lanes": {"old": {"project_url": "old"}}}
    target.write_text(json.dumps(original), encoding="utf-8")

    def boom(url, timeout):
        raise TimeoutError("backend down")

    ok = pull_routing_config("https://aliecs.example/v1/routing/wechat-projects.json", target, opener=boom)

    assert ok is False
    assert json.loads(target.read_text(encoding="utf-8")) == original
