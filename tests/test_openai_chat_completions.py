from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.routes_chat import build_prompt_from_messages
from src.main import create_app


class FakeBrowser:
    def __init__(self, *, started: bool = True, start_fails: bool = False) -> None:
        self.started = started
        self.page = object() if started else None
        self.start_calls = 0
        self.last_error = None
        self._start_fails = start_fails
        self.lane_keys: list[str] = []

    async def start(self) -> None:
        self.start_calls += 1
        if self._start_fails:
            raise RuntimeError("CDP unavailable")
        self.started = True
        self.page = object()

    async def stop(self) -> None:
        self.started = False
        self.page = None

    async def page_for_lane(self, lane):
        self.lane_keys.append(lane.key)
        return f"page:{lane.key}"


class FakePage:
    def __init__(self, url: str = "https://chatgpt.com/") -> None:
        self._url = url

    @property
    def url(self) -> str:
        return self._url

    async def goto(self, url: str, **kwargs) -> None:
        self._url = url

    async def wait_for_selector(self, selector: str, **kwargs):
        return object()


class FakeBrowserWithPage(FakeBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.lane_page = FakePage()

    async def page_for_lane(self, lane):
        self.lane_keys.append(lane.key)
        return self.lane_page


async def fake_ask(self, message: str, **_kwargs) -> tuple[str, float]:
    return f"answer for: {message}", 0.1


async def fake_ask_with_page(self, message: str, **_kwargs) -> tuple[str, float]:
    return f"answer from {self.page}: {message}", 0.1


async def fake_ask_sets_conversation_url(self, message: str, **_kwargs) -> tuple[str, float]:
    self.page._url = "https://chatgpt.com/g/g-p-lark/c/conv-feishu-1"
    return f"answer from {self.page.url}: {message}", 0.1


def make_client(monkeypatch, *, browser: FakeBrowser | None = None) -> tuple[TestClient, FakeBrowser]:
    from src.browser import lane_scheduler

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", fake_ask)
    app = create_app(start_browser=False)
    fake_browser = browser or FakeBrowser()
    app.state.browser = fake_browser
    return TestClient(app), fake_browser


def test_openai_models_returns_browser_chatgpt(monkeypatch):
    client, _ = make_client(monkeypatch)

    response = client.get("/v1/models", headers={"Authorization": "Bearer change-me"})

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {
                "id": "browser-chatgpt",
                "object": "model",
                "created": 0,
                "owned_by": "webdock",
            }
        ],
    }


def test_openai_prompt_builder_uses_last_user_message_for_simple_request():
    prompt = build_prompt_from_messages(
        [
            {"role": "system", "content": "Reply in Chinese"},
            {"role": "user", "content": "first"},
            {"role": "user", "content": "hello"},
        ]
    )

    assert prompt == "Reply in Chinese\n\nhello"


def test_openai_prompt_builder_removes_openclaw_metadata_prefix():
    prompt = build_prompt_from_messages(
        [
            {
                "role": "user",
                "content": 'Conversation info (untrusted metadata):\n```json\n{"chat_id":"abc","message_id":"def"}\n```\n\nbridge test',
            }
        ]
    )

    assert prompt == "bridge test"


def test_openai_prompt_builder_removes_unfenced_openclaw_metadata_prefix():
    prompt = build_prompt_from_messages(
        [
            {
                "role": "user",
                "content": (
                    "[Fri 2026-06-12 02:55 UTC] Conversation info (untrusted metadata):\n"
                    "json\n"
                    '{\n'
                    '  "chat_id": "o9cq80whD47YZs0xR1Y9Ih8rdVnc@im.wechat",\n'
                    '  "message_id": "openclaw-weixin:1781232935667-3a8642ac",\n'
                    '  "timestamp": "Fri 2026-06-12 02:55:35 UTC"\n'
                    "}\n"
                    "/新对话 现在几点了？"
                ),
            }
        ]
    )

    assert prompt == "/新对话 现在几点了？"


def test_openai_prompt_builder_accepts_text_content_list():
    prompt = build_prompt_from_messages(
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "list text"}],
            }
        ]
    )

    assert prompt == "list text"


def test_openai_chat_completion_stream_false_returns_json(monkeypatch):
    client, _ = make_client(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer change-me"},
        json={
            "model": "browser-chatgpt",
            "stream": False,
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.7,
            "max_tokens": 1000,
            "metadata": {"source": "openclaw"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["model"] == "browser-chatgpt"
    assert payload["choices"][0]["message"]["content"] == "answer for: hello"


def test_openai_chat_completion_stream_true_returns_sse(monkeypatch):
    client, _ = make_client(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer change-me"},
        json={
            "model": "browser-chatgpt",
            "stream": True,
            "messages": [{"role": "user", "content": "stream hello"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "data: " in text
    assert "answer for: stream hello" in text
    assert "finish_reason" in text
    assert "data: [DONE]" in text


def test_openai_chat_completion_rejects_tools_with_clear_error(monkeypatch):
    client, _ = make_client(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer change-me"},
        json={
            "model": "browser-chatgpt",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function"}],
        },
    )

    assert response.status_code == 400
    assert "does not support tools/tool_choice" in response.json()["detail"]["message"]


def test_openai_chat_completion_auto_attach_once_then_reports_clear_error(monkeypatch):
    client, browser = make_client(monkeypatch, browser=FakeBrowser(started=False, start_fails=True))

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer change-me"},
        json={
            "model": "browser-chatgpt",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert browser.start_calls == 1
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "BROWSER_NOT_STARTED"
    assert "CDP attach failed" in response.json()["detail"]["message"]


_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_openai_chat_completion_accepts_vision_image_and_uploads(monkeypatch):
    from src.browser import lane_scheduler

    uploaded: list[tuple[object, list[str]]] = []

    async def fake_upload(page, images):
        uploaded.append((page, list(images)))
        return len(images)

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", fake_ask_with_page)
    monkeypatch.setattr(lane_scheduler, "upload_images", fake_upload)
    app = create_app(start_browser=False)
    app.state.browser = FakeBrowser()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer change-me"},
        json={
            "model": "browser-chatgpt",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "把这张图改成卡通风格"},
                        {"type": "image_url", "image_url": {"url": _PNG_DATA_URL}},
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(uploaded) == 1
    _page, images = uploaded[0]
    assert images == [_PNG_DATA_URL]
    assert response.json()["choices"][0]["message"]["content"].endswith("把这张图改成卡通风格")


def test_openai_chat_completion_accepts_image_only_message(monkeypatch):
    from src.browser import lane_scheduler

    async def fake_upload(page, images):
        return len(images)

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", fake_ask)
    monkeypatch.setattr(lane_scheduler, "upload_images", fake_upload)
    app = create_app(start_browser=False)
    app.state.browser = FakeBrowser()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer change-me"},
        json={
            "model": "browser-chatgpt",
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": _PNG_DATA_URL}}]}
            ],
        },
    )

    # An image with no text used to 400 ("Only plain text content is supported").
    assert response.status_code == 200


def test_openai_chat_completion_routes_metadata_to_wechat_lane(monkeypatch):
    from src.browser import lane_scheduler

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", fake_ask_with_page)
    app = create_app(start_browser=False)
    fake_browser = FakeBrowser()
    app.state.browser = fake_browser
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer change-me"},
        json={
            "model": "browser-chatgpt",
            "messages": [{"role": "user", "content": "hello A"}],
            "metadata": {
                "wechat_account": "A",
                "chat_type": "private",
                "peer_id": "user-1",
                "chatgpt_project": "WeChat-A",
            },
        },
    )

    assert response.status_code == 200
    assert fake_browser.lane_keys == ["wechat:A:private:user-1"]
    assert response.json()["choices"][0]["message"]["content"] == (
        "answer from page:wechat:A:private:user-1: hello A"
    )


def test_openai_chat_completion_returns_lane_conversation_metadata(monkeypatch):
    from src.browser import lane_scheduler

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", fake_ask_sets_conversation_url)
    app = create_app(start_browser=False)
    fake_browser = FakeBrowserWithPage()
    app.state.browser = fake_browser
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer change-me"},
        json={
            "model": "browser-chatgpt",
            "messages": [{"role": "user", "content": "hello feishu"}],
            "metadata": {
                "channel": "feishu",
                "chat_type": "group",
                "peer_id": "group:oc_group1",
                "chatgpt_project_url": "https://chatgpt.com/g/g-p-lark/project",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == (
        "answer from https://chatgpt.com/g/g-p-lark/c/conv-feishu-1: hello feishu"
    )
    assert body["metadata"]["chatgpt_conversation_url"] == "https://chatgpt.com/g/g-p-lark/c/conv-feishu-1"
    assert body["metadata"]["lane"]["key"] == "feishu:group:oc_group1"
