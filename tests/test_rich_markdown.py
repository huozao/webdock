import asyncio

from src.browser.detector import rich_assistant_markdown


class _FakePage:
    """Minimal page stub: returns a canned value from evaluate()."""

    def __init__(self, result):
        self._result = result

    async def evaluate(self, _js):
        return self._result


def test_rich_assistant_markdown_returns_stripped_markdown():
    page = _FakePage("# 标题\n\n- a\n- b\n\n")
    out = asyncio.run(rich_assistant_markdown(page))
    assert out == "# 标题\n\n- a\n- b"


def test_rich_assistant_markdown_falls_back_when_empty():
    # Empty markdown -> fall back to rich_assistant_text (which also evaluates the
    # plain-text JS on the same fake page and returns its cleaned value).
    page = _FakePage("")
    out = asyncio.run(rich_assistant_markdown(page))
    assert out == ""
