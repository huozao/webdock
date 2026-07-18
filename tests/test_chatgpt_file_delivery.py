from __future__ import annotations

import asyncio

from src.browser.chatgpt_page import ChatGPTPage
from src.browser.file_download import DownloadedFile, DownloadTarget


class FakeMediaStore:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, str | None]] = []

    def put(self, data: bytes, content_type: str = "image/png", filename: str | None = None) -> str:
        self.calls.append((data, content_type, filename))
        return f"tok-{len(self.calls)}"


def test_append_generated_files_outputs_file_markers(monkeypatch):
    async def fake_targets(page):
        return [
            DownloadTarget(kind="button", filename="answer.pdf", href=None),
            DownloadTarget(kind="button", filename="answer.pdf", href=None),
            DownloadTarget(kind="button", filename="notes.txt", href=None),
        ]

    async def fake_download(page, target):
        return DownloadedFile(
            filename=target.filename,
            content_type="application/pdf" if target.filename.endswith(".pdf") else "text/plain",
            data=f"data:{target.filename}".encode(),
        )

    import src.browser.chatgpt_page as chatgpt_page_module

    monkeypatch.setattr(chatgpt_page_module, "generated_file_targets", fake_targets)
    monkeypatch.setattr(chatgpt_page_module, "download_chatgpt_file", fake_download)

    store = FakeMediaStore()
    page = ChatGPTPage(object(), media_store=store, channel="feishu")

    result = asyncio.run(page._append_generated_files("answer", "https://webdock.example", set()))

    assert result == (
        "answer\n"
        "FILE: https://webdock.example/media/tok-1 name=answer.pdf mime=application/pdf\n"
        "FILE: https://webdock.example/media/tok-2 name=notes.txt mime=text/plain"
    )
    assert store.calls == [
        (b"data:answer.pdf", "application/pdf", "answer.pdf"),
        (b"data:notes.txt", "text/plain", "notes.txt"),
    ]


def test_append_generated_files_delivers_image_pill_as_media(monkeypatch):
    # An image referenced as a file pill is delivered as MEDIA (inline picture),
    # and the pill's bare-filename line is dropped from the reply text.
    async def fake_targets(page):
        return [DownloadTarget(kind="button", filename="scene_o.png", href=None)]

    async def fake_download(page, target):
        return DownloadedFile(filename=target.filename, content_type="image/png", data=b"P" * 2048)

    import src.browser.chatgpt_page as chatgpt_page_module

    monkeypatch.setattr(chatgpt_page_module, "generated_file_targets", fake_targets)
    monkeypatch.setattr(chatgpt_page_module, "download_chatgpt_file", fake_download)

    store = FakeMediaStore()
    page = ChatGPTPage(object(), media_store=store, channel="feishu")

    result = asyncio.run(page._append_generated_files("scene_o.png", "https://webdock.example", set()))

    assert result == "MEDIA: https://webdock.example/media/tok-1"
    assert store.calls == [(b"P" * 2048, "image/png", "scene_o.png")]
