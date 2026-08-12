from __future__ import annotations

import asyncio
import threading

import pytest

from fastapi.testclient import TestClient

from src.api.routes_chat import _status_code_for_error, build_prompt_from_messages
from src.api.chat_jobs import ChatJobStore, JobCapacityError
from src.main import create_app
from src.utils.errors import ErrorCode


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


def test_lane_busy_maps_to_http_429():
    assert _status_code_for_error(ErrorCode.LANE_BUSY) == 429


def test_async_job_submit_returns_before_browser_finishes_and_can_be_polled(monkeypatch):
    from src.browser import lane_scheduler

    started = threading.Event()
    release = threading.Event()

    async def blocking_ask(self, message: str, **_kwargs):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return f"answer for: {message}", 12.5

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", blocking_ask)
    app = create_app(start_browser=False)
    app.state.browser = FakeBrowser()
    body = {
        "model": "browser-chatgpt",
        "messages": [{"role": "user", "content": "long task"}],
        "metadata": {"channel": "feishu", "peer_id": "group:async-test"},
    }

    with TestClient(app) as client:
        app.state.browser = FakeBrowser()
        submitted = client.post(
            "/v1/chat/jobs",
            json=body,
            headers={"Authorization": "Bearer change-me", "X-Request-ID": "req-async-1"},
        )

        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]
        assert submitted.json()["status"] in {"queued", "running"}
        assert started.wait(timeout=1)
        running = client.get(
            f"/v1/chat/jobs/{job_id}",
            headers={"Authorization": "Bearer change-me"},
        )
        assert running.status_code == 200
        assert running.json()["status"] == "running"
        assert "result" not in running.json()

        release.set()
        for _ in range(100):
            finished = client.get(
                f"/v1/chat/jobs/{job_id}",
                headers={"Authorization": "Bearer change-me"},
            )
            if finished.json()["status"] == "succeeded":
                break
            threading.Event().wait(0.01)

        payload = finished.json()
        assert payload["status"] == "succeeded"
        assert payload["result"]["choices"][0]["message"]["content"] == "answer for: long task"


def test_async_job_submit_is_idempotent_by_request_id(monkeypatch):
    from src.browser import lane_scheduler

    calls: list[str] = []

    async def counted_ask(self, message: str, **_kwargs):
        calls.append(message)
        return "done", 0.1

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", counted_ask)
    app = create_app(start_browser=False)
    body = {
        "model": "browser-chatgpt",
        "messages": [{"role": "user", "content": "same request"}],
        "metadata": {"channel": "feishu", "peer_id": "group:async-idempotent"},
    }

    with TestClient(app) as client:
        app.state.browser = FakeBrowser()
        headers = {"Authorization": "Bearer change-me", "X-Request-ID": "req-same"}
        first = client.post("/v1/chat/jobs", json=body, headers=headers)
        second = client.post("/v1/chat/jobs", json=body, headers=headers)

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json()["job_id"] == first.json()["job_id"]
        for _ in range(100):
            state = client.get(
                f"/v1/chat/jobs/{first.json()['job_id']}",
                headers={"Authorization": "Bearer change-me"},
            ).json()
            if state["status"] == "succeeded":
                break
            threading.Event().wait(0.01)
        assert calls == ["same request"]


def test_async_job_rejects_invalid_probe_id(monkeypatch):
    client, _ = make_client(monkeypatch)

    response = client.post(
        "/v1/chat/jobs",
        json={"messages": [{"role": "user", "content": "probe"}]},
        headers={
            "Authorization": "Bearer change-me",
            "X-Request-ID": "req-probe-invalid",
            "X-Webdock-Probe-ID": "../unsafe",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "INVALID_PROBE_ID"


def test_async_job_rejects_probe_when_runtime_switch_is_off(monkeypatch):
    client, _ = make_client(monkeypatch)

    response = client.post(
        "/v1/chat/jobs",
        json={"messages": [{"role": "user", "content": "probe"}]},
        headers={
            "Authorization": "Bearer change-me",
            "X-Request-ID": "req-probe-disabled",
            "X-Webdock-Probe-ID": "probe-disabled",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "DIAGNOSTIC_PROBE_DISABLED"


def test_probe_id_participates_in_job_idempotency_fingerprint(monkeypatch):
    from src.api import routes_chat

    monkeypatch.setattr(routes_chat, "diagnostic_probe_enabled", lambda: True)
    client, _ = make_client(monkeypatch)
    body = {"messages": [{"role": "user", "content": "probe"}]}
    base_headers = {
        "Authorization": "Bearer change-me",
        "X-Request-ID": "req-probe-conflict",
    }

    first = client.post(
        "/v1/chat/jobs",
        json=body,
        headers={**base_headers, "X-Webdock-Probe-ID": "probe-one"},
    )
    second = client.post(
        "/v1/chat/jobs",
        json=body,
        headers={**base_headers, "X-Webdock-Probe-ID": "probe-two"},
    )

    assert first.status_code == 202
    assert second.status_code == 409


def test_async_job_rejects_request_id_reuse_with_different_payload(monkeypatch):
    from src.browser import lane_scheduler

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", fake_ask)
    app = create_app(start_browser=False)
    headers = {"Authorization": "Bearer change-me", "X-Request-ID": "req-conflict"}

    with TestClient(app) as client:
        app.state.browser = FakeBrowser()
        first = client.post(
            "/v1/chat/jobs",
            json={"messages": [{"role": "user", "content": "first"}]},
            headers=headers,
        )
        second = client.post(
            "/v1/chat/jobs",
            json={"messages": [{"role": "user", "content": "different"}]},
            headers=headers,
        )

        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "REQUEST_ID_CONFLICT"


def test_async_job_status_preserves_structured_lane_busy_error(monkeypatch):
    from src.browser import lane_scheduler
    from src.utils.errors import RelayError

    async def busy_ask(self, message: str, **_kwargs):
        raise RelayError(ErrorCode.LANE_BUSY, "已等待 5.0s，本次请求未执行。")

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", busy_ask)
    app = create_app(start_browser=False)

    with TestClient(app) as client:
        app.state.browser = FakeBrowser()
        submitted = client.post(
            "/v1/chat/jobs",
            json={"messages": [{"role": "user", "content": "busy"}]},
            headers={"Authorization": "Bearer change-me", "X-Request-ID": "req-busy-job"},
        )
        job_id = submitted.json()["job_id"]
        for _ in range(100):
            state = client.get(
                f"/v1/chat/jobs/{job_id}",
                headers={"Authorization": "Bearer change-me"},
            ).json()
            if state["status"] == "failed":
                break
            threading.Event().wait(0.01)

        assert state["status"] == "failed"
        assert state["error"]["error_code"] == "LANE_BUSY"
        assert "已等待 5.0s" in state["error"]["message"]


def test_async_job_can_be_cancelled(monkeypatch):
    from src.browser import lane_scheduler

    started = threading.Event()

    async def blocking_ask(self, message: str, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(lane_scheduler.ChatGPTPage, "ask", blocking_ask)
    app = create_app(start_browser=False)

    with TestClient(app) as client:
        app.state.browser = FakeBrowser()
        submitted = client.post(
            "/v1/chat/jobs",
            json={"messages": [{"role": "user", "content": "cancel me"}]},
            headers={"Authorization": "Bearer change-me", "X-Request-ID": "req-cancel-job"},
        )
        job_id = submitted.json()["job_id"]
        assert started.wait(timeout=1)

        cancelled = client.delete(
            f"/v1/chat/jobs/{job_id}",
            headers={"Authorization": "Bearer change-me"},
        )

        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["error"]["error_code"] == "REQUEST_CANCELLED"


def test_job_cancel_before_first_task_step_is_terminal():
    async def scenario():
        runner_called = False

        async def runner():
            nonlocal runner_called
            runner_called = True
            return {"ok": True}

        store = ChatJobStore()
        submitted = await store.submit(
            request_id="cancel-before-start", fingerprint="same", runner=runner
        )
        cancelled = await store.cancel(submitted["job_id"])
        await asyncio.sleep(0)

        assert cancelled["status"] == "cancelled"
        assert (await store.get(submitted["job_id"]))["status"] == "cancelled"
        assert runner_called is False

    asyncio.run(scenario())


def test_job_store_bounds_inflight_tasks_and_entire_lifecycle():
    async def scenario():
        release = asyncio.Event()

        async def blocked_runner():
            await release.wait()
            return {"ok": True}

        store = ChatJobStore(execution_timeout_seconds=0.02, max_active_jobs=1)
        first = await store.submit(
            request_id="bounded-1", fingerprint="one", runner=blocked_runner
        )
        with pytest.raises(JobCapacityError):
            await store.submit(
                request_id="bounded-2", fingerprint="two", runner=blocked_runner
            )

        await asyncio.sleep(0.04)
        state = await store.get(first["job_id"])
        assert state["status"] == "failed"
        assert state["error"]["error_code"] == "RESPONSE_TIMEOUT"

    asyncio.run(scenario())


def test_job_store_prunes_completed_records_when_jobs_finish():
    async def scenario():
        store = ChatJobStore(max_completed_jobs=2)

        async def runner():
            return {"ok": True}

        submitted = []
        for index in range(5):
            submitted.append(
                await store.submit(
                    request_id=f"completed-{index}", fingerprint=str(index), runner=runner
                )
            )
        await asyncio.sleep(0.02)

        retained = [await store.get(item["job_id"]) for item in submitted]
        assert sum(item is not None for item in retained) == 2

    asyncio.run(scenario())


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
