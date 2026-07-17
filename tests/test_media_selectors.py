from src.browser.chatgpt_page import WIDGET_SELECTOR, _media_screenshot_selectors
from src.browser.detector import _ORDERED_MARKDOWN_JS


def test_wechat_screenshots_widgets_only():
    # WeChat path is unchanged in phase 1: only widget renderers, via live capture.
    assert _media_screenshot_selectors("wechat") == [(WIDGET_SELECTOR, False)]


def test_feishu_also_screenshots_tables():
    # Feishu can't render pipe tables, so tables are delivered as screenshots too.
    sels = _media_screenshot_selectors("feishu")
    assert sels[0] == (WIDGET_SELECTOR, False)
    assert any("table" in sel for sel, _prefer_clone in sels)


def test_feishu_table_uses_clone_render():
    # element.screenshot() crops ChatGPT's sticky-header/first-column tables (the
    # frozen parts render outside the <table> box), so tables must go through the
    # clone+inline-styles render path — prefer_clone=True.
    sels = _media_screenshot_selectors("feishu")
    table_selector, prefer_clone = next((s, c) for s, c in sels if "table" in s)
    assert prefer_clone is True


def test_feishu_table_selector_excludes_tables_inside_widgets():
    # A table rendered inside a widget must not be captured twice.
    sels = _media_screenshot_selectors("feishu")
    table_selector = next(s for s, _prefer_clone in sels if "table" in s)
    assert ":not(" in table_selector and "WidgetRenderer" in table_selector


def test_ordered_rich_reply_drops_widget_text_source():
    # Widget text (hourly weather rows, clock digits, etc.) is represented by the
    # screenshot slot and must not also leak into the response_url text stream.
    assert 'cls.indexOf("WidgetRenderer") >= 0' in _ORDERED_MARKDOWN_JS
    assert 'cls.indexOf("not-markdown") >= 0' in _ORDERED_MARKDOWN_JS
    assert 'getAttribute("data-w-component")' in _ORDERED_MARKDOWN_JS
