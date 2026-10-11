from src.browser.detector import (
    _GENERATED_IMG_SRCS_JS,
    _MARK_EXISTING_REPLY_MEDIA_JS,
    _ORDERED_MARKDOWN_JS,
    _RICH_MARKDOWN_JS,
    _RICH_TEXT_JS,
    _TURN_ACTIONS_READY_JS,
)


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


def test_generated_image_scan_is_limited_to_turns_created_after_mark(rich_markdown_page) -> None:
    old_src = "https://chatgpt.com/backend-api/estuary/content?id=old"
    new_src = "https://chatgpt.com/backend-api/estuary/content?id=new"
    rich_markdown_page.set_content(
        "<div id='thread'>"
        "<div data-testid='conversation-turn-1'><div data-message-author-role='assistant'>"
        f"<img alt='Generated image' src='{old_src}'>"
        "</div></div></div>"
    )
    assert rich_markdown_page.evaluate(_GENERATED_IMG_SRCS_JS, 200) == [old_src]

    rich_markdown_page.evaluate(_MARK_EXISTING_REPLY_MEDIA_JS)
    rich_markdown_page.evaluate(
        """(src) => {
          const turn = document.createElement('div');
          turn.setAttribute('data-testid', 'conversation-turn-2');
          turn.innerHTML = `<div data-message-author-role="assistant"><img alt="Generated image" src="${src}"></div>`;
          document.querySelector('#thread').appendChild(turn);
        }""",
        new_src,
    )

    assert rich_markdown_page.evaluate(_GENERATED_IMG_SRCS_JS, 200) == [new_src]


def test_latest_widget_is_not_hidden_by_location_footer(rich_markdown_page) -> None:
    rich_markdown_page.set_content(
        "<div data-testid='conversation-turn-2' data-turn='assistant'>"
        "<div class='WidgetRenderer'>weather</div>"
        "</div>"
        "<div data-testid='conversation-turn-location-footer'>"
        "<button>使用精确位置</button>"
        "</div>"
    )
    turn = rich_markdown_page.locator(
        "[data-testid^='conversation-turn-']:not([data-testid='conversation-turn-location-footer'])"
    ).last
    assert turn.locator("[class*='WidgetRenderer']").count() == 1
    assert rich_markdown_page.evaluate(_ORDERED_MARKDOWN_JS).strip() == "@@WEBDOCK_SLOT_0@@"


def test_virtualized_turn_2026_10_extracts_markdown(rich_markdown_page) -> None:
    html = """
    <div data-virtualized-turn-content="true" data-turn-key="test-key-1">
      <div data-user-message-bubble="true">
        <div>用户提问</div>
      </div>
      <div>
        <span data-chatgpt-agent-turn-start=""></span>
        <h4 data-conversation-role="assistant">ChatGPT said:</h4>
        <div data-markdown-text-style="assistant-message" class="MarkdownRoot-rZKhxa">
          <h2>回答标题</h2>
          <p>这是<strong>加粗正文</strong>与列表：</p>
          <ul>
            <li>选项一</li>
            <li>选项二</li>
          </ul>
        </div>
        <div>
          <button type="button" aria-label="Copy">复制</button>
        </div>
      </div>
    </div>
    """
    rich_markdown_page.set_content(html)
    text = rich_markdown_page.evaluate(_RICH_TEXT_JS).strip()
    assert "回答标题" in text
    assert "选项一" in text

    md = rich_markdown_page.evaluate(_RICH_MARKDOWN_JS).strip()
    assert "## 回答标题" in md
    assert "**加粗正文**" in md
    assert "- 选项一" in md
    assert "- 选项二" in md

    assert rich_markdown_page.evaluate(_TURN_ACTIONS_READY_JS) is True


def test_virtualized_turn_user_only_returns_empty(rich_markdown_page) -> None:
    html = """
    <div data-virtualized-turn-content="true" data-turn-key="test-key-2">
      <div data-user-message-bubble="true">
        <div>用户刚刚发送的问题，助手尚未回复</div>
      </div>
    </div>
    """
    rich_markdown_page.set_content(html)
    assert rich_markdown_page.evaluate(_RICH_TEXT_JS).strip() == ""
    assert rich_markdown_page.evaluate(_RICH_MARKDOWN_JS).strip() == ""


def test_wrapper_containing_text_and_table_preserves_both(rich_markdown_page) -> None:
    # 2026-10 ChatGPT DOM: a shared outer wrapper (div.my-4) wraps all paragraphs
    # AND the table. The ordered walk must NOT drop the paragraphs just because
    # the outer div wraps the table slot.
    html = """
    <div data-virtualized-turn-content="true" data-turn-key="test-key-3">
      <div>
        <h4 data-conversation-role="assistant">ChatGPT said:</h4>
        <div data-markdown-text-style="assistant-message" class="MarkdownRoot-rZKhxa">
          <div class="my-4 first:mt-0 last:mb-0 empty:hidden">
            <p>这是回答的第一段正文。</p>
            <table>
              <thead><tr><th>工具</th><th>用途</th></tr></thead>
              <tbody><tr><td>Codex</td><td>编程</td></tr></tbody>
            </table>
            <p>这是回答的第二段总结。</p>
          </div>
        </div>
      </div>
    </div>
    """
    rich_markdown_page.set_content(html)
    out = rich_markdown_page.evaluate(_ORDERED_MARKDOWN_JS).strip()
    assert "这是回答的第一段正文。" in out
    assert "@@WEBDOCK_SLOT_0@@" in out
    assert "这是回答的第二段总结。" in out
    # 且 slot 处于正文一与总结之间
    pos_p1 = out.index("这是回答的第一段正文。")
    pos_slot = out.index("@@WEBDOCK_SLOT_0@@")
    pos_p2 = out.index("这是回答的第二段总结。")
    assert pos_p1 < pos_slot < pos_p2


def test_nested_divs_in_list_item_preserves_text(rich_markdown_page) -> None:
    # 2026-10 ChatGPT DOM: li > div.Content > div.Label > p > span
    html = """
    <div data-virtualized-turn-content="true" data-turn-key="test-key-4">
      <div>
        <h4 data-conversation-role="assistant">ChatGPT said:</h4>
        <div data-markdown-text-style="assistant-message" class="MarkdownRoot-rZKhxa">
          <ol>
            <li>
              <div class="Content-OKAHK6">
                <div class="Label-B5tj_e">
                  <p><strong>第一项</strong>：详细说明内容。</p>
                </div>
              </div>
            </li>
          </ol>
        </div>
      </div>
    </div>
    """
    rich_markdown_page.set_content(html)
    out = rich_markdown_page.evaluate(_ORDERED_MARKDOWN_JS).strip()
    assert "1. **第一项**：详细说明内容。" in out


def test_modern_chatgpt_codeblock_extraction(rich_markdown_page) -> None:
    html = """
    <div data-virtualized-turn-content="true" data-turn-key="test-key-code">
      <div>
        <h4 data-conversation-role="assistant">ChatGPT said:</h4>
        <div data-markdown-text-style="assistant-message" class="MarkdownRoot-rZKhxa">
          <p>前置说明：</p>
          <div>
            <div class="CodeBlock-zu1QM3">
              <div data-markdown-copy="code-block" class="Surface-dfq13n">
                <div data-markdown-copy="exclude" class="StickyActionBar-XvAQxh">
                  <div class="min-w-0 flex-1 truncate">Bash</div>
                  <button aria-label="Copy">复制</button>
                </div>
                <div class="chatgpt-code-scrollport">
                  <code><span>shop orders export --type refund</span></code>
                </div>
              </div>
            </div>
          </div>
          <p>后置说明。</p>
        </div>
      </div>
    </div>
    """
    rich_markdown_page.set_content(html)
    out = rich_markdown_page.evaluate(_ORDERED_MARKDOWN_JS).strip()
    assert "前置说明：" in out
    assert "```bash\nshop orders export --type refund\n```" in out
    assert "后置说明。" in out


def test_modern_chatgpt_dil_grid_component_slot(rich_markdown_page) -> None:
    html = """
    <div data-virtualized-turn-content="true" data-turn-key="test-key-grid">
      <div>
        <h4 data-conversation-role="assistant">ChatGPT said:</h4>
        <div data-markdown-text-style="assistant-message" class="MarkdownRoot-rZKhxa">
          <p>架构介绍如下：</p>
          <div class="Box-qIMv6G" data-d-component="box" data-d-has-border="">
            <div class="Grid-CGRmTR" data-d-component="grid">
              <div data-d-component="grid-item">GUI</div>
              <div data-d-component="grid-item">CLI</div>
            </div>
          </div>
          <p>总结评价。</p>
        </div>
      </div>
    </div>
    """
    rich_markdown_page.set_content(html)
    out = rich_markdown_page.evaluate(_ORDERED_MARKDOWN_JS).strip()
    assert "架构介绍如下：" in out
    assert "@@WEBDOCK_SLOT_0@@" in out
    assert "总结评价。" in out



