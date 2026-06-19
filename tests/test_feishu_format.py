from __future__ import annotations

from src.browser.feishu_format import feishu_safe_markdown


def test_feishu_safe_markdown_preserves_unordered_list_text():
    markdown = "你要承担：\n\n- 海外仓库存压货\n- 退货退款\n- 品牌授权/IP 风险"

    assert feishu_safe_markdown(markdown) == (
        "你要承担：\n\n"
        "• 海外仓库存压货\n"
        "• 退货退款\n"
        "• 品牌授权/IP 风险"
    )


def test_feishu_safe_markdown_preserves_ordered_list_text_without_post_md_list_syntax():
    markdown = "你要做：\n\n1. 注册/了解 Temu 卖家后台\n2. 选 20 个候选品\n8. 预算控制在 **5-15 万人民币以内**"

    assert feishu_safe_markdown(markdown) == (
        "你要做：\n\n"
        "1\\. 注册/了解 Temu 卖家后台\n"
        "2\\. 选 20 个候选品\n"
        "8\\. 预算控制在 **5-15 万人民币以内**"
    )


def test_feishu_safe_markdown_preserves_task_lists_and_skips_code_fences():
    markdown = "清单：\n\n- [x] 已完成\n- [ ] 未完成\n\n```text\n- keep code\n1. keep code\n```"

    assert feishu_safe_markdown(markdown) == (
        "清单：\n\n"
        "☑ 已完成\n"
        "☐ 未完成\n\n"
        "```text\n- keep code\n1. keep code\n```"
    )
