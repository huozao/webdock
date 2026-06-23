from __future__ import annotations

import asyncio

import src.browser.detector as detector


class FakePage:
    """Returns a scripted sequence of markdown-JS results; a distinct sentinel for
    the plain-text fallback JS so we can tell a retry from a fallback."""

    def __init__(self, md_results, text_result="FALLBACK_FLAT"):
        self._md = list(md_results)
        self._text = text_result
        self.md_calls = 0
        self.text_calls = 0

    async def evaluate(self, js):
        if js is detector._RICH_MARKDOWN_JS:
            self.md_calls += 1
            return self._md.pop(0) if self._md else ""
        if js is detector._RICH_TEXT_JS:
            self.text_calls += 1
            return self._text
        return ""


def test_rich_markdown_retries_then_returns_markdown():
    # Markdown DOM walk yields empty twice (table still settling) then succeeds.
    page = FakePage(md_results=["", "", "# real\n\n| a | b |"])
    out = asyncio.run(detector.rich_assistant_markdown(page, attempts=3, settle_seconds=0))
    assert out == "# real\n\n| a | b |"
    assert page.md_calls == 3
    assert page.text_calls == 0  # never silently fell back to flattened text


def test_rich_markdown_falls_back_after_attempts_exhausted():
    page = FakePage(md_results=["", "", ""])
    out = asyncio.run(detector.rich_assistant_markdown(page, attempts=3, settle_seconds=0))
    assert out == "FALLBACK_FLAT"
    assert page.md_calls == 3
    assert page.text_calls == 1
