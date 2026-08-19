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
        self.selectors: list[str] = []
        self.image_preview_scans = 0
        # A document pill click opens the preview flyout — that is what makes the
        # short first download wait safe to give up on.
        self.flyout_visible = True
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
        page.selectors.append(selector)

        class _Loc:
            def filter(self, has_text: str | None = None):
                return self

            @property
            def first(self):
                return self

            async def click(self, timeout: int = 0) -> None:
                page.clicked = True

            async def is_visible(self, timeout: int = 0) -> bool:
                return page.flyout_visible

        return _Loc()

    async def evaluate(self, script: str, arg: object | None = None):
        if "preview-layer-present" in script:
            return self.flyout_visible
        if "arrayBuffer" in script:
            return self._b64
        if "clientWidth" in script:
            self.image_preview_scans += 1
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
    # The overlay is dismissed through its own close control; Escape does not
    # close the document flyout (2026-07-27) and is only the last-ditch fallback.
    assert any("close-button" in selector for selector in page.selectors)


def test_prose_labelled_image_pill_is_recovered_from_the_new_lightbox():
    """2026-08-17 生产：ChatGPT 用代码工具改完图后给的是「下载 800×800 图片」，
    没有扩展名，于是被当成文档——白等 68s、飞书只收到文字没有图。新版预览层
    `modal-lightbox-new` 没有任何 Download 控件（只有 Close/Save/Share），但它
    渲染着 484×484 的原图，抓这张图才是正解。"""
    payload = b"\x89PNG\r\n\x1a\n" + b"P" * 2048
    page = _FakePillPage(payload)
    target = DownloadTarget(kind="button", filename="下载 800×800 图片", href=None)

    file = asyncio.run(_download_button(page, target))

    assert file is not None
    assert file.data == payload
    # Named from the bytes, not the label: an extension-less name would guess
    # application/octet-stream and ship the picture as a file card.
    assert file.content_type == "image/png"
    assert file.filename.endswith(".png")
    assert any("close-button" in selector for selector in page.selectors)


def test_prose_labelled_pill_does_not_pay_the_document_budget():
    """没有扩展名 ≠ 文档：旧代码给它 10s+50s 的文档预算，再花 5s 点一个不存在的
    下载控件。预览层一开就该立刻转向，绝不进入剩余预算的等待。"""
    page = _FakePillPage(b"\x89PNG\r\n\x1a\n" + b"P" * 2048)
    late_waits: list[str] = []

    async def _record_wait(event: str, timeout: int = 0):
        late_waits.append(event)
        raise TimeoutError("Timeout waiting for event download")

    page.wait_for_event = _record_wait

    asyncio.run(_download_button(page, DownloadTarget(kind="button", filename="下载 800×800 图片", href=None)))

    assert late_waits == []


def test_download_button_document_never_scans_for_a_preview_image():
    # A document pill opens ChatGPT's preview flyout, so we try the flyout's own
    # Download control — never the preview-IMAGE scan, which is image-only and
    # would grab the wrong thing. When that produces no download event either,
    # the result stays None and the flyout is dismissed rather than left covering
    # the page for the next reply.
    page = _FakePillPage(b"D" * 2048)
    target = DownloadTarget(kind="button", filename="report.pdf", href=None)

    file = asyncio.run(_download_button(page, target))

    assert file is None
    assert page.image_preview_scans == 0
    # Dismissed via the flyout's own close control — Escape does not close it.
    assert any("close-button" in selector for selector in page.selectors)


class _FakeFlyoutPage(_FakePillPage):
    """Document pill click opens the preview flyout without firing a download;
    the flyout's own Download control is what produces the file."""

    def __init__(self, payload: bytes, download_path) -> None:
        super().__init__(payload)
        self._download_path = download_path
        self.download_waits = 0

    def expect_download(self, timeout: int = 0):
        self.download_waits += 1
        pill_click = self.download_waits == 1
        path = self._download_path

        class _Download:
            suggested_filename = "report.pdf"

            async def path(self):
                return path

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> None:
                if pill_click:
                    raise TimeoutError("Timeout waiting for event download")

            @property
            def value(self):
                async def _resolve():
                    return _Download()

                return _resolve()

        return _Ctx()


class _SlowDirectDownloadPage(_FakePillPage):
    """Document pill that really does download, just slower than the short first
    wait — and opens no flyout, so giving up early would drop the file."""

    def __init__(self, payload: bytes, download_path) -> None:
        super().__init__(payload)
        self.flyout_visible = False
        self._download_path = download_path
        self.late_waits = 0

    async def wait_for_event(self, event: str, timeout: int = 0):
        self.late_waits += 1
        path = self._download_path

        class _Download:
            suggested_filename = "report.pdf"

            async def path(self):
                return path

        return _Download()


def test_document_pill_keeps_waiting_when_no_flyout_opened(tmp_path):
    """The short first wait only exists to detect the flyout case. With no flyout,
    the remaining budget is still spent on the download we clicked for."""
    payload = b"S" * 2048
    downloaded = tmp_path / "report.pdf"
    downloaded.write_bytes(payload)
    page = _SlowDirectDownloadPage(payload, downloaded)
    target = DownloadTarget(kind="button", filename="report.pdf", href=None)

    file = asyncio.run(_download_button(page, target))

    assert page.late_waits == 1
    assert file is not None
    assert file.data == payload
    # No flyout was open, so its download control is never clicked or dismissed.
    assert not any("close-button" in selector for selector in page.selectors)


def test_document_pill_with_flyout_does_not_burn_the_rest_of_the_budget(tmp_path):
    """With the flyout up, no download event will ever fire — switch to it at once
    instead of waiting out the full 60s (2026-07-28: 60 of a 224s turn wasted)."""
    payload = b"D" * 2048
    downloaded = tmp_path / "report.pdf"
    downloaded.write_bytes(payload)
    page = _FakeFlyoutPage(payload, downloaded)
    late_waits: list[str] = []

    async def _record_wait(event: str, timeout: int = 0):
        late_waits.append(event)
        raise TimeoutError("Timeout waiting for event download")

    page.wait_for_event = _record_wait

    file = asyncio.run(_download_button(page, DownloadTarget(kind="button", filename="report.pdf", href=None)))

    assert late_waits == []  # never fell into the remaining-budget wait
    assert file is not None
    assert file.data == payload


def test_document_pill_downloads_via_preview_flyout(tmp_path):
    payload = b"D" * 2048
    downloaded = tmp_path / "report.pdf"
    downloaded.write_bytes(payload)
    page = _FakeFlyoutPage(payload, downloaded)
    target = DownloadTarget(kind="button", filename="report.pdf", href=None)

    file = asyncio.run(_download_button(page, target))

    assert file is not None
    assert file.data == payload
    assert file.filename == "report.pdf"
    # The second click targets the flyout's download control, not the pill again.
    assert any("aria-label" in selector for selector in page.selectors)
    # …and the flyout is dismissed afterwards via its own close control.
    assert any("close-button" in selector for selector in page.selectors)


def test_parse_accepts_any_generated_format():
    # Whatever ChatGPT generates (origin-gated) is downloadable — .json/.zip/.py
    # are normal deliverables and must not be dropped by a format whitelist.
    raw = [
        {"kind": "link", "href": "sandbox:/mnt/data/data.json", "text": "data.json"},
        {"kind": "link", "href": "sandbox:/mnt/data/bundle.zip", "text": "bundle.zip"},
        {"kind": "button", "href": "", "text": "analysis.py", "download": ""},
    ]

    targets = parse_download_targets(raw)

    assert [t.filename for t in targets] == ["data.json", "bundle.zip", "analysis.py"]


def test_parse_accepts_estuary_file_service_links():
    # The current ChatGPT file service serves images AND documents from
    # /backend-api/estuary/content?id=file_...&sig=... (real URLs, 2026-07-18).
    raw = [
        {
            "kind": "link",
            "href": "https://chatgpt.com/backend-api/estuary/content?id=file_000000000e5471f5ba998ee401832701&ts=495649&p=fs&cid=1&sig=51c9612d&v=0",
            "text": "scene.png",
        },
        {
            "kind": "link",
            "href": "https://chatgpt.com/backend-api/estuary/content?id=file_00000000698071fd9b303bea145afabf&ts=495649&p=fs&cid=1&sig=94ba301e&v=0",
            "text": "report.pdf",
        },
        {"kind": "link", "href": "https://files.oaiusercontent.com/file-abc?sig=x", "text": "notes.docx"},
        # estuary path on a foreign host is NOT ChatGPT's file service
        {"kind": "link", "href": "https://evil.example/backend-api/estuary/content?id=file_x", "text": "trap.pdf"},
    ]

    targets = parse_download_targets(raw)

    assert [t.filename for t in targets] == ["scene.png", "report.pdf", "notes.docx"]


def test_parse_rejects_executable_formats():
    raw = [
        {"kind": "link", "href": "sandbox:/mnt/data/tool.exe", "text": "tool.exe"},
        {"kind": "button", "href": "", "text": "setup.msi", "download": ""},
    ]

    assert parse_download_targets(raw) == []


def test_parse_rejects_sentence_label_with_stray_dot():
    # "v2.0 说明" has a dot but is not a filename — must not be clicked.
    raw = [{"kind": "button", "href": "", "text": "v2.0 说明", "download": ""}]

    assert parse_download_targets(raw) == []


def test_parse_rejects_non_download_button():
    # Buttons that are not download affordances (reasoning toggle, copy, …) must
    # never be clicked — otherwise every reply pays a download timeout.
    raw = [
        {"kind": "button", "href": "", "text": "已思考 1m 44s", "download": ""},
        {"kind": "button", "href": "", "text": "copy"},
    ]

    assert parse_download_targets(raw) == []


class _FirstClickMissesPage(_FakePillPage):
    """2026-08-18 生产：第一次点 pill 什么也没发生——detector 在答案完成那一帧
    就返回，页面还在收尾重渲，点击落到了即将被 React 替换的节点上，既没有
    download 也没有预览层。旧代码直接去抓图，对着没开的层空转 5s，交付了一条
    没有图的回复。第二次点击才把层点开。"""

    opens_on_click = 2  # which pill click actually opens the layer

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.flyout_visible = False
        self.clicks = 0

    def locator(self, selector: str):
        page = self
        page.selectors.append(selector)
        is_pill = selector == "button"
        is_close = "close-button" in selector or "Close" in selector

        class _Loc:
            def filter(self, has_text: str | None = None):
                return self

            @property
            def first(self):
                return self

            async def click(self, timeout: int = 0) -> None:
                if is_pill:
                    page.clicks += 1
                    page.clicked = True
                    if page.opens_on_click and page.clicks >= page.opens_on_click:
                        page.flyout_visible = True
                    return
                if is_close:
                    page.flyout_visible = False
                    return
                # The lightbox carries no Download control — clicking it times out.
                raise TimeoutError("Timeout waiting for locator")

            async def is_visible(self, timeout: int = 0) -> bool:
                return page.flyout_visible

        return _Loc()

    async def evaluate(self, script: str, arg: object | None = None):
        if "inTurn" in script:  # the diagnostics scan, not the src probe
            return []
        if "clientWidth" in script and not self.flyout_visible:
            self.image_preview_scans += 1
            return None  # nothing to grab while the layer is closed
        return await super().evaluate(script, arg)


def test_image_pill_is_clicked_again_when_the_first_click_opened_nothing(monkeypatch):
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_WAIT_SECONDS", 0.01)
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_TOTAL_BUDGET_SECONDS", 0.06)
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_POLL_SECONDS", 0.01)
    payload = b"\x89PNG\r\n\x1a\n" + b"P" * 2048
    page = _FirstClickMissesPage(payload)
    target = DownloadTarget(kind="button", filename="下载 800×800 图片", href=None)

    file = asyncio.run(_download_button(page, target))

    assert page.clicks == 2, "第一次点击落空后必须再点一次，否则整轮回复丢图"
    assert file is not None
    assert file.data == payload


class _NeverOpensPage(_FirstClickMissesPage):
    """重点一次仍然打不开：这时必须留下取证行，别再静默丢图。"""

    opens_on_click = 0  # no click ever opens the layer


def test_capture_failure_is_logged_with_candidates(monkeypatch, caplog):
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_WAIT_SECONDS", 0.01)
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_TOTAL_BUDGET_SECONDS", 0.06)
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_POLL_SECONDS", 0.01)
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LATE_LAYER_GRACE_SECONDS", 0)
    page = _NeverOpensPage(b"\x89PNG\r\n\x1a\n" + b"P" * 2048)
    target = DownloadTarget(kind="button", filename="下载 800×800 图片", href=None)

    with caplog.at_level("WARNING"):
        file = asyncio.run(_download_button(page, target))

    assert file is None
    assert any("preview image capture failed" in record.message for record in caplog.records)


class _LateLayerPage(_FirstClickMissesPage):
    """层在我们放弃之后才渲染出来：08-19 生产实测，用户发现页面停在全屏预览上，
    而这一轮的回复里没有图。晚到的层既要救回这张图，也必须被关掉——留着会盖住
    会话，下一轮永远等不到完成信号。"""

    opens_on_click = 0  # clicking never opens it in time

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self._late_pending = False

    async def evaluate(self, script: str, arg: object | None = None):
        if "preview-layer-present" in script:
            if self._late_pending:  # the layer finally renders, after we gave up
                self._late_pending = False
                self.flyout_visible = True
            return self.flyout_visible
        if "inTurn" in script:
            return []
        if "clientWidth" in script:
            self.image_preview_scans += 1
            if self.image_preview_scans <= 10:  # the whole first capture comes up empty
                if self.image_preview_scans == 10:
                    self._late_pending = True
                return None
            return "https://chatgpt.com/backend-api/estuary/content?id=file_PREVIEW"
        return await super().evaluate(script, arg)


def test_late_arriving_layer_is_captured_and_closed(monkeypatch, caplog):
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_WAIT_SECONDS", 0.01)
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_TOTAL_BUDGET_SECONDS", 0.06)
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LAYER_POLL_SECONDS", 0.01)
    monkeypatch.setattr("src.browser.file_download.PREVIEW_LATE_LAYER_GRACE_SECONDS", 0)
    payload = b"\x89PNG\r\n\x1a\n" + b"P" * 2048
    page = _LateLayerPage(payload)
    target = DownloadTarget(kind="button", filename="下载 800×800 图片", href=None)

    with caplog.at_level("WARNING"):
        file = asyncio.run(_download_button(page, target))

    assert file is not None and file.data == payload
    assert any("arrived late" in record.message for record in caplog.records)
    # 关层必须发生，否则下一轮被这张全屏预览盖死
    assert any("close-button" in selector or "Close" in selector for selector in page.selectors)


class _CardControlPage:
    """The file card's own download button: this one really downloads."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.clicked_index: int | None = None
        self.dispatched = False
        page = self

        class _Download:
            suggested_filename = "包装更新_800x800_最新版.pdf"

            async def path(self):
                return None

            async def failure(self):
                return None

        self._download = _Download()

    def locator(self, selector: str):
        page = self

        class _Loc:
            def nth(self, index: int):
                page.clicked_index = index
                return self

            @property
            def first(self):
                return self

            async def click(self, timeout: int = 0) -> None:
                raise AssertionError("must not use a real click: the control is pointer-events:none")

            async def evaluate(self, script: str) -> None:
                page.dispatched = True

        return _Loc()

    def expect_download(self, timeout: int = 0):
        page = self

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> None:
                return None

            @property
            def value(self):
                async def _v():
                    return page._download
                return _v()

        return _Ctx()


def test_card_download_control_is_preferred_over_the_prose_link(monkeypatch):
    """2026-08-19：整个月的丢图都是点错了控件。文件卡片右侧的 Download file 按钮
    实测 4.96s 就产生真实 download 事件、文件名正确、且不会把标签页导航走；而它上面
    那条带下划线的「下载 PDF 文件」只会打开预览层。有卡片控件时必须走它。"""
    from src.browser.file_download import download_chatgpt_file

    captured = {}

    async def _fake_read(download, target):
        captured["download"] = download
        captured["target"] = target
        return None

    monkeypatch.setattr("src.browser.file_download._read_download", _fake_read)
    page = _CardControlPage(b"P" * 2048)
    target = DownloadTarget(kind="button", filename="包装更新.pdf", href=None, control_index=2)

    asyncio.run(download_chatgpt_file(page, target))

    assert page.clicked_index == 2, "必须点第 index 个卡片控件，而不是第一个"
    assert page.dispatched, "必须在页面内派发 click：控件是 pointer-events:none，真实点击点不到"
    assert captured["download"] is page._download
