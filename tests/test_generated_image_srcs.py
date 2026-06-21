from __future__ import annotations

from src.browser.detector import _GENERATED_IMG_SRCS_JS


# Real ChatGPT multi-image-reply DOM, mirrored from the 2026-06-21 CDP probe on
# the live page: the assistant turn carries ONE main 480x480 displayed image plus
# a 48x48 side-rail thumbnail per extra candidate (alt="已生成图片" on both).
# Without recognizing the thumbnails, only the main view reaches the user.
_MULTI_CANDIDATE_HTML = """
<div data-testid="conversation-turn-1">
  <div data-message-author-role="user">
    <img src="https://chatgpt.com/backend-api/files/upload_a.jpg"
         alt="webdock-upload-a.jpg" width="128" height="128"
         style="width:128px;height:128px;">
  </div>
</div>
<div data-testid="conversation-turn-2">
  <div style="position:relative;width:480px;height:480px;">
    <img src="https://chatgpt.com/backend-api/estuary/content?id=file_MAIN"
         alt="已生成图片" width="480" height="480"
         style="width:480px;height:480px;">
  </div>
  <div style="position:relative;width:48px;height:48px;">
    <img src="https://chatgpt.com/backend-api/estuary/content?id=file_CAND_A"
         alt="已生成图片" width="48" height="48"
         style="width:48px;height:48px;">
  </div>
  <div style="position:relative;width:48px;height:48px;">
    <img src="https://chatgpt.com/backend-api/estuary/content?id=file_CAND_B"
         alt="Generated image" width="48" height="48"
         style="width:48px;height:48px;">
  </div>
</div>
"""


def test_collects_small_alt_marked_candidates(rich_markdown_page):
    rich_markdown_page.set_content(_MULTI_CANDIDATE_HTML)

    srcs = rich_markdown_page.evaluate(_GENERATED_IMG_SRCS_JS, 200)

    assert any("file_MAIN" in s for s in srcs), srcs
    assert any("file_CAND_A" in s for s in srcs), srcs
    assert any("file_CAND_B" in s for s in srcs), srcs
    assert not any("upload_a" in s for s in srcs), srcs


def test_ignores_small_unmarked_backend_images(rich_markdown_page):
    # Small estuary src without the "generated image" alt (e.g. UI decoration)
    # must still be filtered — the alt rule narrows the carve-out, it does not
    # open the floodgates to every backend-api img.
    rich_markdown_page.set_content(
        """
        <div data-testid="conversation-turn-1">
          <img src="https://chatgpt.com/backend-api/estuary/content?id=icon_x"
               alt="" width="32" height="32"
               style="width:32px;height:32px;">
        </div>
        """
    )

    srcs = rich_markdown_page.evaluate(_GENERATED_IMG_SRCS_JS, 200)

    assert srcs == [], srcs


def test_keeps_large_main_image_without_alt(rich_markdown_page):
    # Defensive: even if a future ChatGPT version drops the alt text, the size-based
    # rule alone still captures the main displayed image.
    rich_markdown_page.set_content(
        """
        <div data-testid="conversation-turn-1">
          <div style="position:relative;width:480px;height:480px;">
            <img src="https://chatgpt.com/backend-api/estuary/content?id=file_BARE_MAIN"
                 alt="" width="480" height="480"
                 style="width:480px;height:480px;">
          </div>
        </div>
        """
    )

    srcs = rich_markdown_page.evaluate(_GENERATED_IMG_SRCS_JS, 200)

    assert any("file_BARE_MAIN" in s for s in srcs), srcs
