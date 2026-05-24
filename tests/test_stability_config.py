from src.browser.manager import build_chromium_args, jitter_viewport
from src.config import Settings


def test_chromium_args_include_stability_flags():
    args = build_chromium_args(Settings())

    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args
    assert "--disable-dev-shm-usage" in args


def test_viewport_jitter_stays_near_base_size():
    viewport = jitter_viewport(1366, 768, jitter=20)

    assert 1346 <= viewport["width"] <= 1386
    assert 748 <= viewport["height"] <= 788
