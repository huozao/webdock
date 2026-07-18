from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from src.browser.detector import _DOWNLOAD_SCAN_JS
from src.browser.file_download import _download_button, DownloadTarget, parse_download_targets


RAW_FIXTURE = Path(__file__).parent / "fixtures" / "feishu" / "raw" / "download_files.html"


def test_download_scan_finds_chatgpt_generated_file_buttons(rich_markdown_page):
    rich_markdown_page.set_content(RAW_FIXTURE.read_text(encoding="utf-8"))

    raw = rich_markdown_page.evaluate(_DOWNLOAD_SCAN_JS)
    targets = parse_download_targets(raw)

    assert [target.filename for target in targets] == [
        "feishu_test.txt",
        "feishu_test.pdf",
        "feishu_test.docx",
    ]
    assert all(target.kind == "button" for target in targets)


def test_parse_download_targets_allows_only_chatgpt_generated_files():
    raw = [
        {"kind": "link", "href": "https://evil.example/report.pdf", "text": "report.pdf"},
        {"kind": "link", "href": "https://chatgpt.com/cdn/report.pdf", "text": "report.pdf"},
        {"kind": "button", "text": "copy"},
        {"kind": "link", "href": "sandbox:/mnt/data/report.pdf", "text": "report.pdf"},
        {
            "kind": "link",
            "href": "https://chatgpt.com/backend-api/files/file-abc/download",
            "download": "answer.docx",
        },
    ]

    targets = parse_download_targets(raw)

    assert [(target.kind, target.filename, target.href) for target in targets] == [
        ("link", "report.pdf", "sandbox:/mnt/data/report.pdf"),
        ("link", "answer.docx", "https://chatgpt.com/backend-api/files/file-abc/download"),
    ]


def test_parse_accepts_localized_download_button():
    # Real ChatGPT renders the generated-file button with a localized ACTION label
    # ("下载 PDF 扫描件"), not a filename. The real name/extension only arrive with
    # the download event, so the button must be accepted on download intent alone.
    raw = [{"kind": "button", "href": "", "text": "下载 PDF 扫描件", "download": ""}]

    targets = parse_download_targets(raw)

    assert len(targets) == 1
    assert targets[0].kind == "button"
    assert targets[0].href is None


def test_parse_accepts_generated_image_pill():
    # "重新发我" replies reference the earlier picture as a filename pill
    # (button.behavior-btn with the .png name as its label) — must be a target.
    raw = [{
        "kind": "button", "href": "",
        "text": "a_bright_warm_glossy_food_advertisement_scene_o.png", "download": "",
    }]

    targets = parse_download_targets(raw)

    assert len(targets) == 1
    assert targets[0].kind == "button"
    assert targets[0].filename.endswith(".png")


class _FakePillPage:
    """Image pill click: no download event fires; a preview overlay opens instead."""

    def __init__(self, payload: bytes) -> None:
        self._b64 = base64.b64encode(payload).decode()
        self.clicked = False
        self.pressed: list[str] = []
        page = self

        class _Keyboard:
            async def press(self, key: str) -> None:
                page.pressed.append(key)

        self.keyboard = _Keyboard()

    def expect_download(self, timeout: int = 0):
        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> None:
                raise TimeoutError("Timeout waiting for event download")

        return _Ctx()

    def locator(self, selector: str):
        page = self

        class _Loc:
            def filter(self, has_text: str | None = None):
                return self

            @property
            def first(self):
                return self

            async def click(self, timeout: int = 0) -> None:
                page.clicked = True

        return _Loc()

    async def evaluate(self, script: str, arg: object | None = None):
        if "arrayBuffer" in script:
            return self._b64
        if "clientWidth" in script:
            return "https://chatgpt.com/backend-api/estuary/content?id=file_PREVIEW"
        return None


def test_download_button_falls_back_to_preview_capture_for_images():
    payload = b"P" * 2048
    page = _FakePillPage(payload)
    target = DownloadTarget(kind="button", filename="scene.png", href=None)

    file = asyncio.run(_download_button(page, target))

    assert page.clicked
    assert file is not None
    assert file.filename == "scene.png"
    assert file.content_type == "image/png"
    assert file.data == payload
    assert "Escape" in page.pressed  # the preview overlay is closed afterwards


def test_download_button_no_preview_fallback_for_documents():
    # A document button with no download event must stay None (no preview scan).
    page = _FakePillPage(b"D" * 2048)
    target = DownloadTarget(kind="button", filename="report.pdf", href=None)

    file = asyncio.run(_download_button(page, target))

    assert file is None
    assert page.pressed == []


def test_parse_rejects_non_download_button():
    # Buttons that are not download affordances (reasoning toggle, copy, …) must
    # never be clicked — otherwise every reply pays a download timeout.
    raw = [
        {"kind": "button", "href": "", "text": "已思考 1m 44s", "download": ""},
        {"kind": "button", "href": "", "text": "copy"},
    ]

    assert parse_download_targets(raw) == []
