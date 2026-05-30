from __future__ import annotations

import asyncio
import random
from typing import Any


async def random_delay(min_ms: int, max_ms: int) -> None:
    if max_ms <= 0:
        return
    lower = max(0, min_ms)
    upper = max(lower, max_ms)
    await asyncio.sleep(random.randint(lower, upper) / 1000)


async def paste_text(page: Any, selector: str, text: str, *, delay_min_ms: int = 0, delay_max_ms: int = 0) -> None:
    """Enter text into ChatGPT's ProseMirror contenteditable and verify it landed.

    keyboard.insert_text() alone is flaky on the ProseMirror editor: when the
    editor is not fully ready (e.g. the page loaded slowly) it can silently
    no-op, leaving the input empty so ChatGPT receives nothing and the reply
    never comes back. We try several strategies, clearing and re-reading the
    editor between attempts, and raise if none of them landed the text so the
    caller fails fast (with a debug dump) instead of sending an empty prompt.
    Newlines are typed with Shift+Enter so a multi-line message is never sent early.
    """
    element = page.locator(selector).first
    await element.click()
    await random_delay(delay_min_ms, delay_max_ms)

    expected = (text or "").strip()
    if not expected:
        return

    last_seen = ""
    for strategy in ("insert_text", "fill", "type"):
        await _focus_and_clear(page, element)
        try:
            if strategy == "insert_text":
                await element.click()
                await page.keyboard.insert_text(text)
            elif strategy == "fill":
                await element.fill(text)
            else:
                await _type_with_newlines(page, element, text)
        except Exception:
            continue
        last_seen = await _read_editor_text(element)
        if _text_landed(last_seen, expected):
            return

    raise RuntimeError(
        f"Could not enter text into the ChatGPT input box (ProseMirror); editor still shows {last_seen!r}"
    )


async def _focus_and_clear(page: Any, element: Any) -> None:
    try:
        await element.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
    except Exception:
        pass


async def _type_with_newlines(page: Any, element: Any, text: str) -> None:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line:
            await element.press_sequentially(line, delay=10)
        if index < len(lines) - 1:
            await page.keyboard.press("Shift+Enter")


async def _read_editor_text(element: Any) -> str:
    try:
        return (await element.inner_text(timeout=1500)).strip()
    except Exception:
        try:
            return ((await element.text_content(timeout=1500)) or "").strip()
        except Exception:
            return ""


def _text_landed(actual: str, expected: str) -> bool:
    normalized_actual = " ".join((actual or "").split())
    normalized_expected = " ".join((expected or "").split())
    if not normalized_expected:
        return True
    return normalized_expected in normalized_actual or normalized_actual in normalized_expected


async def hover_and_click(page: Any, selector: str) -> None:
    element = page.locator(selector).first
    try:
        await element.hover()
        await asyncio.sleep(random.uniform(0.1, 0.25))
    except Exception:
        pass
    await element.click()


async def idle_mouse_movement(page: Any) -> None:
    try:
        viewport = page.viewport_size
        if not viewport:
            return
        width = max(1, int(viewport["width"]))
        height = max(1, int(viewport["height"]))
        x = random.randint(min(80, width), max(81, width - 80))
        y = random.randint(min(80, height), max(81, height - 80))
        await page.mouse.move(x, y, steps=random.randint(4, 12))
    except Exception:
        pass
