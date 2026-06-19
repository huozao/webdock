from __future__ import annotations

import pytest
from patchright.sync_api import sync_playwright


@pytest.fixture
def rich_markdown_page():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()
