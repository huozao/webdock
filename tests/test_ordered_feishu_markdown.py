from __future__ import annotations

from src.browser.detector import _ORDERED_MARKDOWN_JS


def _turn(inner_html: str) -> str:
    return (
        "<div data-testid='conversation-turn-3'>"
        "<div data-message-author-role='assistant'>"
        f"<div class='markdown'>{inner_html}</div>"
        "</div></div>"
    )


def _evaluate(page, inner_html: str) -> str:
    page.set_content(_turn(inner_html))
    return page.evaluate(_ORDERED_MARKDOWN_JS).strip()


def test_loose_list_items_keep_text(rich_markdown_page) -> None:
    # ChatGPT renders "loose" GFM lists as <li><p>…</p></li>; the ordered walk must
    # not drop the block-wrapped text (2026-07-02 穿衣建议 empty-bullets regression).
    out = _evaluate(
        rich_markdown_page,
        "<h3>穿衣建议</h3>"
        "<ul>"
        "<li><p>白天短袖即可</p></li>"
        "<li><p>早晚加<strong>薄外套</strong></p></li>"
        "</ul>",
    )
    assert out == "### 穿衣建议\n\n- 白天短袖即可\n- 早晚加**薄外套**"


def test_tight_list_unchanged(rich_markdown_page) -> None:
    out = _evaluate(rich_markdown_page, "<ul><li>甲</li><li>乙</li></ul>")
    assert out == "- 甲\n- 乙"


def test_loose_ordered_list_keeps_text_and_numbers(rich_markdown_page) -> None:
    out = _evaluate(
        rich_markdown_page,
        "<ol><li><p>第一步</p></li><li><p>第二步</p></li></ol>",
    )
    assert out == "1. 第一步\n2. 第二步"


def test_loose_nested_list(rich_markdown_page) -> None:
    out = _evaluate(
        rich_markdown_page,
        "<ul><li><p>外层</p><ul><li><p>内层</p></li></ul></li></ul>",
    )
    assert out == "- 外层\n  - 内层"


def test_blockquote_keeps_block_wrapped_text(rich_markdown_page) -> None:
    # ChatGPT renders quotes as <blockquote><p>…</p></blockquote>; emitBlock must
    # recurse into child blocks (2026-07-02 推荐主文案 empty-quote regression).
    out = _evaluate(
        rich_markdown_page,
        "<p>推荐主文案：</p>"
        "<blockquote><p>阿宽魔芋面<br>0脂低卡 | 6味组合 | 开袋即食</p></blockquote>",
    )
    assert out == "推荐主文案：\n\n> 阿宽魔芋面  \n> 0脂低卡 | 6味组合 | 开袋即食"


def test_blockquote_with_multiple_paragraphs(rich_markdown_page) -> None:
    out = _evaluate(
        rich_markdown_page,
        "<blockquote><p>第一段</p><p>第二段</p></blockquote>",
    )
    assert out == "> 第一段\n> \n> 第二段"


def test_blockquote_bare_text_still_works(rich_markdown_page) -> None:
    out = _evaluate(rich_markdown_page, "<blockquote>裸文本引用</blockquote>")
    assert out == "> 裸文本引用"
