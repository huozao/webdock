import asyncio

from src.browser.detector import _strip_markdown_tables, rich_assistant_markdown


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


def test_strip_markdown_tables_removes_pipe_table_keeps_prose():
    # A real GFM pipe table (header + delimiter + body) is removed so Feishu shows
    # the table as a screenshot instead of flattened text; prose is preserved.
    md = (
        "前言\n\n"
        "| 对比项 | Python | JavaScript |\n"
        "| --- | --- | --- |\n"
        "| 用途 | 数据/AI | 前端 |\n"
        "| 语法 | 缩进 | 花括号 |\n\n"
        "结语"
    )
    out = _strip_markdown_tables(md)
    assert "对比项" not in out and "|" not in out
    assert out == "前言\n\n结语"


def test_strip_markdown_tables_keeps_table_free_markdown():
    md = "# H\n\ntext\n\n- a\n- b"
    assert _strip_markdown_tables(md) == md


def test_strip_markdown_tables_keeps_prose_with_bare_pipe():
    # A stray pipe in prose (no delimiter row) is NOT a table -> keep it.
    md = "用 a | b 表示或运算"
    assert _strip_markdown_tables(md) == md


def test_strip_markdown_tables_removes_trailing_table():
    md = "说明\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert _strip_markdown_tables(md) == "说明"
