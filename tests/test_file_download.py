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
