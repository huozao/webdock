from __future__ import annotations

import asyncio
import json

from src.browser.response_lifecycle_probe import (
    ResponseLifecycleState,
    ResponseLifecycleProbe,
    diagnostic_probe_enabled,
    extract_terminal_markers,
    lifecycle_network_monitor_enabled,
    observe_detector_state,
    response_lifecycle_status_component,
    response_probe_request,
    sanitize_url,
    start_response_probe,
    stop_response_probe,
    validate_probe_id,
)
from src.config import Settings


class FakeSession:
    def __init__(self, body: str = "") -> None:
        self.handlers: dict[str, object] = {}
        self.commands: list[tuple[str, dict]] = []
        self.detached = False
        self.body = body

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def remove_listener(self, event: str, handler) -> None:
        assert self.handlers[event] is handler
        del self.handlers[event]

    async def send(self, method: str, params: dict | None = None):
        self.commands.append((method, params or {}))
        return {"body": self.body, "base64Encoded": False}

    async def detach(self) -> None:
        self.detached = True


class FakeContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def new_cdp_session(self, page):
        return self.session


class FakePage:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.context = FakeContext(session or FakeSession())
        self.evaluate_calls = 0

    async def evaluate(self, _script):
        self.evaluate_calls += 1
        return {
            "assistant_turn_present": True,
            "turn_signature": {"tag": "article", "role": "", "testid": "conversation-turn-2"},
            "animated_candidates": [
                {
                    "tag": "div",
                    "role": "status",
                    "testid": "",
                    "class_tokens": ["shimmer"],
                    "animation_names": ["shimmer"],
                }
            ],
        }


def test_probe_id_validation_is_path_safe():
    assert validate_probe_id("probe_2026-08-12.short") == "probe_2026-08-12.short"
    assert validate_probe_id("../unsafe") is None
    assert validate_probe_id("x" * 65) is None
    assert validate_probe_id("") is None


def test_url_sanitization_drops_query_and_fragment():
    assert sanitize_url("https://chatgpt.com/backend-api/f/conversation?token=secret#x") == (
        "https://chatgpt.com/backend-api/f/conversation"
    )
    assert sanitize_url("not a url") == ""


def test_url_sanitization_redacts_conversation_uuid():
    assert sanitize_url(
        "https://chatgpt.com/backend-api/conversation/6a7c47a4-cef0-83ea-b935-c409fbc7cc6b/stream_status"
    ) == "https://chatgpt.com/backend-api/conversation/:uuid/stream_status"


def test_terminal_extraction_keeps_only_allowlisted_protocol_fields():
    body = "\n".join(
        [
            'event: message',
            'data: {"type":"response.completed","status":"completed","message":"secret reply"}',
            'data: {"event":"thread.run.failed","detail":"private"}',
            "data: [DONE]",
        ]
    )

    markers = extract_terminal_markers(body)

    assert {tuple(sorted(marker.items())) for marker in markers} == {
        (("field", "type"), ("value", "response.completed")),
        (("field", "status"), ("value", "completed")),
        (("field", "event"), ("value", "thread.run.failed")),
        (("field", "sentinel"), ("value", "DONE")),
    }
    assert "secret" not in json.dumps(markers)
    assert "private" not in json.dumps(markers)


def test_probe_trace_is_bounded_and_never_persists_sensitive_values(tmp_path):
    probe = ResponseLifecycleProbe("bounded", tmp_path, max_events=2, max_bytes=4096)

    probe.record("first", url="https://chatgpt.com/path?token=secret")
    probe.record("second", headers={"authorization": "secret"}, body="reply secret")
    probe.record("third", value="must-not-fit")
    asyncio.run(probe.close("completed"))

    raw = (tmp_path / "bounded.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    assert [event["event"] for event in events] == ["first", "second", "probe_truncated"]
    assert "token=secret" not in raw
    assert "authorization" not in raw
    assert "reply secret" not in raw
    assert "must-not-fit" not in raw


def test_same_page_probe_enables_only_network_and_always_detaches(tmp_path):
    async def scenario():
        session = FakeSession()
        page = FakePage(session)
        with response_probe_request("same-page", tmp_path):
            probe = await start_response_probe(page)
            assert probe is not None
            await observe_detector_state(
                page,
                stop_present=True,
                streaming_present=True,
                action_row_present=False,
                assistant_count=1,
                generated_image_count=0,
                widget_present=False,
            )
            await stop_response_probe(probe, "cancelled")

        assert session.commands == [("Network.enable", {})]
        assert session.detached is True
        assert session.handlers == {}
        events = [
            json.loads(line)
            for line in (tmp_path / "same-page.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert events[0]["event"] == "probe_start"
        assert any(event["event"] == "dom_state" for event in events)
        assert events[-1]["event"] == "probe_end"
        assert events[-1]["outcome"] == "cancelled"

    asyncio.run(scenario())


def test_close_waits_for_terminal_body_parse_before_probe_end(tmp_path):
    async def scenario():
        session = FakeSession('data: {"status":"completed","message":"secret"}')
        page = FakePage(session)
        with response_probe_request("terminal-order", tmp_path):
            probe = await start_response_probe(page)
            assert probe is not None
            probe.record("send_started")
            session.handlers["Network.requestWillBeSent"](
                {
                    "requestId": "r1",
                    "type": "Fetch",
                    "request": {"url": "https://chatgpt.com/backend-api/f/conversation?token=secret"},
                }
            )
            session.handlers["Network.responseReceived"](
                {
                    "requestId": "r1",
                    "response": {"status": 200, "mimeType": "text/event-stream"},
                }
            )
            session.handlers["Network.loadingFinished"](
                {"requestId": "r1", "encodedDataLength": 123}
            )
            await stop_response_probe(probe, "completed")

        raw = (tmp_path / "terminal-order.jsonl").read_text(encoding="utf-8")
        events = [json.loads(line) for line in raw.splitlines()]
        names = [event["event"] for event in events]
        assert names.index("protocol_terminal") < names.index("probe_end")
        assert "secret" not in raw
        assert "token=" not in raw

    asyncio.run(scenario())


def test_protocol_terminals_ignore_pre_send_and_unrelated_endpoints(tmp_path):
    async def scenario():
        session = FakeSession('data: {"status":"failed"}')
        page = FakePage(session)
        with response_probe_request("correlated-terminal", tmp_path):
            probe = await start_response_probe(page)
            assert probe is not None
            session.handlers["Network.requestWillBeSent"](
                {
                    "requestId": "before",
                    "type": "Fetch",
                    "request": {"url": "https://chatgpt.com/backend-api/tasks"},
                }
            )
            session.handlers["Network.responseReceived"](
                {"requestId": "before", "response": {"status": 200, "mimeType": "application/json"}}
            )
            session.handlers["Network.loadingFinished"](
                {"requestId": "before", "encodedDataLength": 50}
            )
            await asyncio.sleep(0)

            probe.record("send_started")
            session.body = "data: [DONE]"
            session.handlers["Network.requestWillBeSent"](
                {
                    "requestId": "conversation",
                    "type": "Fetch",
                    "request": {"url": "https://chatgpt.com/backend-api/f/conversation"},
                }
            )
            session.handlers["Network.responseReceived"](
                {
                    "requestId": "conversation",
                    "response": {"status": 200, "mimeType": "text/event-stream"},
                }
            )
            session.handlers["Network.loadingFinished"](
                {"requestId": "conversation", "encodedDataLength": 100}
            )
            await stop_response_probe(probe, "completed")

        events = [
            json.loads(line)
            for line in (tmp_path / "correlated-terminal.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        terminals = [event for event in events if event["event"] == "protocol_terminal"]
        assert [(event["terminal_field"], event["terminal_value"]) for event in terminals] == [
            ("sentinel", "DONE")
        ]

    asyncio.run(scenario())


def test_inactive_detector_observation_does_not_touch_page():
    async def scenario():
        page = FakePage()
        await observe_detector_state(
            page,
            stop_present=False,
            streaming_present=False,
            action_row_present=False,
            assistant_count=0,
            generated_image_count=0,
            widget_present=False,
        )
        assert page.evaluate_calls == 0

    asyncio.run(scenario())


def test_runtime_probe_switch_is_read_fresh(tmp_path):
    runtime_path = tmp_path / "runtime.json"
    settings = Settings(browser_profile_dir=tmp_path)
    assert diagnostic_probe_enabled(settings) is False

    runtime_path.write_text(json.dumps({"diagnostic_probe_enabled": True}), encoding="utf-8")
    assert diagnostic_probe_enabled(settings) is True

    runtime_path.write_text(json.dumps({"diagnostic_probe_enabled": False}), encoding="utf-8")
    assert diagnostic_probe_enabled(settings) is False


def test_runtime_lifecycle_state_is_reusable_without_writing_a_trace(tmp_path):
    async def scenario():
        state = ResponseLifecycleState()
        page = FakePage()
        with response_probe_request(None, tmp_path, lifecycle=state):
            probe = await start_response_probe(page)
            assert probe is not None
            probe.record("send_started")
            probe.record("send_clicked")
            await observe_detector_state(
                page,
                stop_present=True,
                streaming_present=False,
                action_row_present=False,
                assistant_count=1,
                generated_image_count=0,
                widget_present=False,
            )
            probe._record_protocol_markers(
                "ws-1", [{"field": "type", "value": "done"}], "websocket_unmapped"
            )
            await observe_detector_state(
                page,
                stop_present=False,
                streaming_present=False,
                action_row_present=True,
                assistant_count=1,
                generated_image_count=0,
                widget_present=False,
            )
            snapshot = state.snapshot()
            assert snapshot["schema_version"] == 1
            assert snapshot["phase"] == "finalizing"
            assert snapshot["server_terminal_observed"] is True
            assert snapshot["stop_present"] is False
            assert snapshot["action_row_present"] is True
            assert state.completion_ready() is True
            await stop_response_probe(probe, "completed")

        assert state.snapshot()["phase"] == "completed"
        assert page.context.session.commands == []
        assert page.evaluate_calls == 0
        assert list(tmp_path.glob("*.jsonl")) == []

    asyncio.run(scenario())


def test_sse_done_does_not_mark_server_terminal():
    state = ResponseLifecycleState()
    state.observe_event("send_started")
    state.observe_event(
        "protocol_terminal",
        source="response_body",
        terminal_field="sentinel",
        terminal_value="DONE",
    )

    assert state.snapshot()["server_terminal_observed"] is False
    assert state.completion_ready() is False


def test_status_component_reads_unknown_when_structure_is_not_sampled(tmp_path):
    # Nobody sampling the DOM structure must read as "unknown", never as "the page
    # stopped working" — otherwise a disabled monitor looks like a wedged turn.
    state = ResponseLifecycleState()
    with response_probe_request(None, tmp_path, lifecycle=state):
        assert response_lifecycle_status_component() is None

        state.structure_available = True
        assert response_lifecycle_status_component() is False

        state.observe_dom(
            stop_present=True,
            action_row_present=False,
            structure={"animated_candidates": [{"role": "status", "class_tokens": []}]},
        )
        assert response_lifecycle_status_component() is True


def test_runtime_network_monitor_requires_explicit_opt_in(tmp_path):
    settings = Settings(browser_profile_dir=tmp_path)
    assert lifecycle_network_monitor_enabled(settings) is False

    (tmp_path / "runtime.json").write_text(
        json.dumps({"lifecycle_network_monitor_enabled": True}), encoding="utf-8"
    )
    assert lifecycle_network_monitor_enabled(settings) is True
