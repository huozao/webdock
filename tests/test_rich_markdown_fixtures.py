from __future__ import annotations

from pathlib import Path

import pytest

from src.browser.detector import _RICH_MARKDOWN_JS


FIXTURES = Path(__file__).parent / "fixtures" / "feishu"


def _cases() -> list[str]:
    return sorted(
        path.stem
        for path in FIXTURES.glob("*.html")
        if (FIXTURES / f"{path.stem}.md").exists()
    )


@pytest.mark.parametrize("name", _cases())
def test_dom_to_markdown(rich_markdown_page, name: str) -> None:
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    golden = (FIXTURES / f"{name}.md").read_text(encoding="utf-8").strip()

    rich_markdown_page.set_content(html)
    out = rich_markdown_page.evaluate(_RICH_MARKDOWN_JS).strip()

    assert out == golden, f"\n--- got ---\n{out}\n--- want ---\n{golden}"
