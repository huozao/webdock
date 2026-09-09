"""失败取证的结构化记录与保留期。

契约见 AliECS/docs/constraints/browser-capture-evidence.md。这里守三条：记录字段与
quota-monitor 的 captures 同名、截图失败不吞原错误、保留期按目录名日期而不是 mtime。
"""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import config
from src.browser import debug_dump
from src.utils.errors import ErrorCode, RelayError


class FakePage:
    def __init__(self, *, shot_fails=False, url="https://chatgpt.com/g/x/project"):
        self.url = url
        self.shot_fails = shot_fails

    async def screenshot(self, *, path, full_page):
        if self.shot_fails:
            raise TimeoutError("screenshot timeout")
        Path(path).write_bytes(b"\x89PNG" + b"0" * 16)

    async def content(self):
        return "<html><body>Try again</body></html>"

    async def title(self):
        return "ChatGPT"


@pytest.fixture
def debug_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG_DIR", str(tmp_path / "debug"))
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("BROWSER_PROFILE_DIR", str(tmp_path / "profile"))
    config.get_settings.cache_clear()
    yield tmp_path / "debug"
    config.get_settings.cache_clear()


def _dump(page, error):
    return asyncio.run(debug_dump.save_debug_dump(page, error))


def test_capture_record_uses_the_shared_field_names(debug_root, monkeypatch):
    monkeypatch.setattr(debug_dump, "any_selector_found", _always(False))
    _dump(FakePage(), RelayError(ErrorCode.RESPONSE_TIMEOUT, "did not finish"))

    directories = [p for p in debug_root.iterdir() if p.is_dir()]
    assert len(directories) == 1
    record = json.loads((directories[0] / "capture.json").read_text(encoding="utf-8"))

    for key in ("captured_at", "url", "status", "confidence", "fields_json",
                "error", "screenshot_path", "screenshot_sha256", "error_code"):
        assert key in record, key
    assert record["captured_at"].endswith("+00:00")
    # 下游判据必须能拿到结构化错误码，而不是去匹配人类可读文案。
    assert record["error_code"] == ErrorCode.RESPONSE_TIMEOUT.value
    assert record["screenshot_sha256"]

    index = json.loads((debug_root / "index.jsonl").read_text(encoding="utf-8").strip())
    assert index["debug_dir"] == directories[0].name


def test_confidence_is_zero_when_the_page_lost_every_structure(debug_root, monkeypatch):
    """一个结构都找不到 = 页面多半被打成了错误边界（09-09 的 /project 空页就是这形状）。"""
    monkeypatch.setattr(debug_dump, "any_selector_found", _always(False))
    _dump(FakePage(), "boom")
    record = _only_record(debug_root)
    assert record["confidence"] == 0.0
    assert record["status"] == "schema_changed"

    for path in sorted(debug_root.iterdir()):
        if path.is_dir():
            for item in path.iterdir():
                item.unlink()
            path.rmdir()
    (debug_root / "index.jsonl").unlink()

    monkeypatch.setattr(debug_dump, "any_selector_found", _always(True))
    _dump(FakePage(), "boom")
    record = _only_record(debug_root)
    assert record["confidence"] == 1.0
    assert record["status"] == "healthy"


def test_screenshot_failure_does_not_replace_the_real_error(debug_root, monkeypatch):
    monkeypatch.setattr(debug_dump, "any_selector_found", _always(True))
    _dump(FakePage(shot_fails=True), RelayError(ErrorCode.UPLOAD_FAILED, "图片未能附加"))

    record = _only_record(debug_root)
    assert record["error"].startswith("图片未能附加")
    assert "screenshot: TimeoutError" in record["error"]
    # 截图没成功就不能留路径，否则下游按路径取图只会拿到 404。
    assert record["screenshot_path"] == ""
    assert record["screenshot_sha256"] == ""


def test_retention_drops_old_snapshots_by_directory_date(debug_root, monkeypatch):
    """⚠️ 按目录名日期判，不按 mtime：回看一个旧快照会改 mtime，按 mtime 判会给它续命。"""
    monkeypatch.setattr(debug_dump, "any_selector_found", _always(True))
    debug_root.mkdir(parents=True, exist_ok=True)
    stale = debug_root / f"{(datetime.now(timezone.utc) - timedelta(days=30)):%Y-%m-%d_%H%M%S}"
    stale.mkdir()
    (stale / "screenshot.png").write_bytes(b"old")
    # 故意把 mtime 刷成"刚刚"，证明判据没有落在 mtime 上。
    os.utime(stale, (time.time(), time.time()))

    _dump(FakePage(), "boom")

    assert not stale.exists()
    lines = (debug_root / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert (debug_root / json.loads(lines[0])["debug_dir"]).is_dir()


def _only_record(debug_root):
    directories = [p for p in debug_root.iterdir() if p.is_dir()]
    assert len(directories) == 1
    return json.loads((directories[0] / "capture.json").read_text(encoding="utf-8"))


def _always(value):
    async def _stub(_page, _selectors):
        return value
    return _stub
