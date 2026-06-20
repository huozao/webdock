from __future__ import annotations

import json

from src.config import Settings, _apply_runtime_overrides


def test_no_runtime_file_keeps_env_defaults(tmp_path):
    s = Settings(browser_profile_dir=tmp_path)
    out = _apply_runtime_overrides(s)
    assert out.chat_timeout_seconds == 120
    assert out.response_stable_seconds == 5
    assert out.response_idle_timeout_seconds == 15
    assert out.response_hard_timeout_seconds == 1200


def test_runtime_override_chat_timeout(tmp_path):
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "chat_timeout_seconds": 200,
                "response_stable_seconds": 6,
                "response_idle_timeout_seconds": 20,
                "response_hard_timeout_seconds": 900,
            }
        ),
        encoding="utf-8",
    )
    s = Settings(browser_profile_dir=tmp_path)
    out = _apply_runtime_overrides(s)
    assert out.chat_timeout_seconds == 200
    assert out.response_stable_seconds == 6
    assert out.response_idle_timeout_seconds == 20
    assert out.response_hard_timeout_seconds == 900
    # original frozen instance is untouched
    assert s.chat_timeout_seconds == 120


def test_runtime_override_ignores_unknown_and_bad_values(tmp_path):
    (tmp_path / "runtime.json").write_text(
        json.dumps({"chat_timeout_seconds": "abc", "unrelated": 1}),
        encoding="utf-8",
    )
    s = Settings(browser_profile_dir=tmp_path)
    out = _apply_runtime_overrides(s)
    assert out.chat_timeout_seconds == 120  # bad value ignored


def test_runtime_override_corrupt_json_is_safe(tmp_path):
    (tmp_path / "runtime.json").write_text("{not valid json", encoding="utf-8")
    s = Settings(browser_profile_dir=tmp_path)
    out = _apply_runtime_overrides(s)
    assert out.chat_timeout_seconds == 120
