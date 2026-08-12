# WebDock Response Lifecycle Probe Implementation Plan

> **For Codex:** Execute this plan directly with TDD. Do not pause for user review; the user explicitly requested direct execution.

**Goal:** Add a default-off, one-request diagnostic probe inside the existing WebDock Playwright worker, then deploy it and collect sanitized DOM/network lifecycle evidence from webdock2 without a second CDP connection or detector behavior changes.

**Architecture:** The async job route accepts a validated probe ID only when the runtime flag is enabled. A request-scoped context follows the job into `ChatGPTPage.ask`, where a probe attaches to the already-owned page, records bounded sanitized events, and always detaches in `finally`. The detector only exports its existing structural state when a probe is active.

**Tech Stack:** Python 3.10, FastAPI, asyncio ContextVar, Patchright/Playwright page events and same-connection CDP Network session, pytest.

---

### Task 1: Lock activation semantics with failing tests

**Files:**
- Modify: `tests/test_runtime_overrides.py`
- Modify: `tests/test_openai_chat_completions.py`
- Create: `tests/test_response_lifecycle_probe.py`

1. Add tests for the `diagnostic_probe_enabled` runtime boolean.
2. Add async-job route tests for invalid probe IDs, disabled probe requests, and fingerprint separation.
3. Add unit tests for URL sanitization, terminal-event extraction, bounded JSONL output, and cleanup that never affects the normal request.
4. Run the focused tests and confirm they fail for missing implementation.

### Task 2: Add the isolated probe implementation

**Files:**
- Create: `src/browser/response_lifecycle_probe.py`
- Modify: `src/config.py`
- Modify: `src/api/routes_chat.py`

1. Add the default-false runtime boolean and a fresh runtime-file read at job submission so the switch can be toggled without restarting Chrome.
2. Validate `X-Webdock-Probe-ID` as `[A-Za-z0-9._-]{1,64}` and reject explicit probe requests when disabled.
3. Carry the probe request through a ContextVar inside the async job runner and include the probe ID in idempotency fingerprinting.
4. Implement a bounded in-memory event recorder flushed to `logs/probes/<id>.jsonl`, sanitizing URLs to origin plus path and never storing bodies, headers, query strings, or assistant/user text.
5. Attach only to the existing page. Use a temporary CDP session created from that page's existing browser context with only `Network.enable`, and detach it in `finally`. If instrumentation fails, log a warning and continue the normal job unchanged.

### Task 3: Wire structural observations without changing decisions

**Files:**
- Modify: `src/browser/chatgpt_page.py`
- Modify: `src/browser/detector.py`
- Modify: `tests/test_response_lifecycle_probe.py`

1. Start the probe before the send path and close it in the outer `finally` for success, error, cancellation, and timeout.
2. Mark the send click and detector completion return, but do not change any detector branch or timeout.
3. From each existing detector loop, send only booleans/counts plus structural animated-element metadata; do not send text or HTML.
4. Prove inactive mode performs no extra page evaluation and active mode emits only state changes.

### Task 4: Verify, publish, and deploy default-off

**Files:**
- Modify only the WebDock source/tests/docs above.
- Update infra image pin only after the WebDock image is published.

1. Run focused tests, then full `pytest -q`.
2. Inspect diff/status/branch/remote and verify no `.env`, logs, browser data, references, secrets, or probe output are tracked.
3. Commit and push WebDock, wait for the image build, pin the immutable digest in infra, commit/push/sync both WebDock hosts, and verify both health endpoints and primary routing.
4. Keep the runtime flag false during ordinary production verification.

### Task 5: Run webdock2 evidence probes and restore disabled state

1. Toggle `diagnostic_probe_enabled=true` only in webdock2 runtime JSON without restarting Chrome.
2. Submit a short isolated-lane async job through the existing authenticated WebDock API and collect its JSONL.
3. Submit one longer isolated-lane job; use cancellation only if the first two do not expose an aborted signal.
4. Confirm listener cleanup, no files for non-probe lanes, and normal health.
5. Toggle the runtime flag back to false, delete probe artifacts after extracting a redacted evidence summary, and verify explicit probe requests are rejected again.
6. Report the observed ordering and use it to revise the later reload/card-status plan.
