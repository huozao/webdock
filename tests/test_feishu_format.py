from __future__ import annotations

from src.browser.feishu_format import feishu_safe_markdown


def test_feishu_safe_markdown_converts_pipe_table_to_aligned_code_block():
    # Feishu lark_md cannot render markdown pipe tables (they flatten to a space
    # blob). Convert them to a CJK-width-aligned monospace block inside a code
    # fence, which lark_md DOES render with columns lined up.
    md = (
        "最好包括：\n\n"
        "| 素材类型 | 建议数量 | 说明 |\n"
        "| --- | ---: | --- |\n"
        "| 食欲图 | 1–3 张 | 面条、碗 |\n"
        "| LOGO | 1 张 | 提取 |\n\n"
        "后语"
    )
    out = feishu_safe_markdown(md)

    assert out.count("```") == 2  # one fenced block
    assert "|" not in out  # no leaked pipe-table syntax
    assert "---" not in out  # separator row dropped
    assert "最好包括：" in out and "后语" in out  # surrounding text kept
    fence = out.split("```")[1].strip().splitlines()
    assert fence[0].split()[0] == "素材类型"  # header first
    assert any(line.startswith("食欲图") for line in fence)
    assert "面条、碗" in out


def test_feishu_safe_markdown_preserves_unordered_list_text():
    markdown = "你要承担：\n\n- 海外仓库存压货\n- 退货退款\n- 品牌授权/IP 风险"

    assert feishu_safe_markdown(markdown) == (
        "你要承担：\n\n"
        "• 海外仓库存压货\n"
        "• 退货退款\n"
        "• 品牌授权/IP 风险"
    )


def test_feishu_safe_markdown_preserves_ordered_list_text_without_post_md_list_syntax():
    # Fullwidth period (．) instead of escaped \\. — Feishu Android renders the
    # backslash literally (you see "1\\."), while a fullwidth period is plain
    # text on every client and still not list syntax (post md cannot fold it).
    markdown = "你要做：\n\n1. 注册/了解 Temu 卖家后台\n2. 选 20 个候选品\n8. 预算控制在 **5-15 万人民币以内**"

    assert feishu_safe_markdown(markdown) == (
        "你要做：\n\n"
        "1． 注册/了解 Temu 卖家后台\n"
        "2． 选 20 个候选品\n"
        "8． 预算控制在 **5-15 万人民币以内**"
    )


def test_feishu_safe_markdown_preserves_task_lists_and_skips_code_fences():
    markdown = "清单：\n\n- [x] 已完成\n- [ ] 未完成\n\n```text\n- keep code\n1. keep code\n```"

    assert feishu_safe_markdown(markdown) == (
        "清单：\n\n"
        "☑ 已完成\n"
        "☐ 未完成\n\n"
        "```text\n- keep code\n1. keep code\n```"
    )
