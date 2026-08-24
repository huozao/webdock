"""上传落地判据对着真实 composer DOM 断言（2026-08-24 生产抓的两个状态）。

fixture 里两个 tile 是同一种元素的两个时刻：一个上传中（`cursor-wait` +
120×120 进度环 + `blob:` 预览），一个已完成（环没了 + 预览换成带 `id=file_` 的
服务端地址）。判据只认这些结构差异，不含尺寸、百分比和时长。

最后一个用例是反证：同一份 DOM 喂给旧判据（数附件 chip 有没有变多），上传中的
tile 照样被数成"已落地"——这正是 08-24 那次在 1% 就点发送的原因。
"""

from pathlib import Path

from src.browser import selectors
from src.browser.chatgpt_page import _ATTACHMENT_STATES_JS

RAW_FIXTURE = Path(__file__).parent / "fixtures" / "feishu" / "raw" / "composer_attachments.html"

UPLOADING = "webdock-upload-aaaaaaaa.jpg"
DONE = "webdock-upload-bbbbbbbb.jpg"
NEVER_LANDED = "webdock-upload-cccccccc.jpg"


def test_states_separate_uploading_from_finished_and_missing(rich_markdown_page):
    rich_markdown_page.set_content(RAW_FIXTURE.read_text(encoding="utf-8"))

    states = rich_markdown_page.evaluate(_ATTACHMENT_STATES_JS, [UPLOADING, DONE, NEVER_LANDED])

    assert states == ["uploading", "done", "missing"]


def test_progress_ring_alone_would_still_be_early(rich_markdown_page):
    """环消失早于 file id 到位（09:11:17 vs 09:11:49），所以两个标记都要。"""
    rich_markdown_page.set_content(RAW_FIXTURE.read_text(encoding="utf-8"))

    ring_gone_but_blob = rich_markdown_page.evaluate(
        """
        (name) => {
          const tile = Array.from(document.querySelectorAll("[role='group'][aria-label]"))
            .find((t) => (t.getAttribute('aria-label') || '').includes(name));
          tile.querySelectorAll('svg').forEach((s) => s.remove());
          tile.querySelectorAll('.cursor-wait').forEach((e) => e.classList.remove('cursor-wait'));
          return true;
        }
        """,
        UPLOADING,
    )
    assert ring_gone_but_blob

    states = rich_markdown_page.evaluate(_ATTACHMENT_STATES_JS, [UPLOADING])
    assert states == ["uploading"], "预览还是 blob: 就不算落地"


def test_old_chip_count_judge_calls_the_1_percent_upload_landed(rich_markdown_page):
    """反证：旧判据在同一份 DOM 上把上传中的附件判成已落地。"""
    rich_markdown_page.set_content(RAW_FIXTURE.read_text(encoding="utf-8"))

    chips = 0
    for selector in selectors.ATTACHMENT_PREVIEW:
        chips += rich_markdown_page.locator(selector).count()

    # 旧判据 = "数量比 baseline(0) 多就算落地"，两个 tile 都被计入，
    # 其中一个还在 1%。新判据对同一份 DOM 只承认其中一个。
    assert chips > 0
    assert rich_markdown_page.evaluate(_ATTACHMENT_STATES_JS, [UPLOADING, DONE]) == [
        "uploading",
        "done",
    ]
