import json
from pathlib import Path

from src.browser.manager import build_chromium_args, jitter_viewport
from src.config import Settings


def test_chromium_args_include_stability_flags():
    args = build_chromium_args(Settings())

    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args
    assert "--disable-session-crashed-bubble" in args
    assert "--hide-crash-restore-bubble" in args
    assert "--disable-crash-reporter" in args
    assert "--disable-dev-shm-usage" in args


def test_viewport_jitter_stays_near_base_size():
    viewport = jitter_viewport(1366, 768, jitter=20)

    assert 1346 <= viewport["width"] <= 1386
    assert 748 <= viewport["height"] <= 788


def test_chrome_policy_disables_metrics_and_session_restore():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root / "docker" / "chrome-managed-policy.json").read_text(encoding="utf-8"))

    assert policy["MetricsReportingEnabled"] is False
    assert policy["RestoreOnStartup"] == 5
