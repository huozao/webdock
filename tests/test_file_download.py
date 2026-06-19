from __future__ import annotations

from pathlib import Path

from src.browser.detector import _DOWNLOAD_SCAN_JS
from src.browser.file_download import parse_download_targets


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


def test_parse_rejects_non_download_button():
    # Buttons that are not download affordances (reasoning toggle, copy, …) must
    # never be clicked — otherwise every reply pays a download timeout.
    raw = [
        {"kind": "button", "href": "", "text": "已思考 1m 44s", "download": ""},
        {"kind": "button", "href": "", "text": "copy"},
    ]

    assert parse_download_targets(raw) == []
