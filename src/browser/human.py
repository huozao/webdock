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
    element = page.locator(selector).first
    await element.click()
    await random_delay(delay_min_ms, delay_max_ms)
    await page.keyboard.insert_text(text)


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
