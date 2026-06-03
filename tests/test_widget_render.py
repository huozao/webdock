from __future__ import annotations

from src.browser.chatgpt_page import _build_render_html, _strip_media_noise


def test_render_html_embeds_widget_in_standalone_doc():
    html = _build_render_html("<div class='WidgetRenderer'>clock</div>")
    assert html.startswith("<!DOCTYPE html>")
    # widget markup is embedded verbatim inside the root container
    assert "<div class='WidgetRenderer'>clock</div>" in html
    # screenshot target + white background so it isn't transparent
    assert "id='webdock-widget-root'" in html
    assert "background:#fff" in html


def test_render_html_root_shrinks_to_content():
    html = _build_render_html("<span>x</span>")
    # inline-block so the root shrinks to the widget -> tightly cropped shot
    assert "display:inline-block" in html


def test_strip_media_noise():
    assert _strip_media_noise("正在思考\n正在生成更细致的图片，请稍候。") == ""
    assert _strip_media_noise("Thought for 35s\n编辑") == ""
    assert _strip_media_noise("粉色梦境中的魅力猪小姐\n\n编辑") == "粉色梦境中的魅力猪小姐"
    assert _strip_media_noise("") == ""
