from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


async def apply_stealth_if_enabled(context: Any, *, enabled: bool) -> None:
    if not enabled:
        log.info("Stealth compatibility patches disabled")
        return

    try:
        from playwright_stealth import Stealth
    except Exception as exc:
        log.warning("playwright-stealth is unavailable; continuing without patches: %s", exc)
        return

    stealth = Stealth()
    if _is_docker_display():
        await _apply_docker_safe_stealth(context, stealth.script_payload)
    else:
        await stealth.apply_stealth_async(context)
    log.info("Browser compatibility patches applied")


def _is_docker_display() -> bool:
    return os.path.exists("/.dockerenv") or os.environ.get("DISPLAY") == ":99"


async def _apply_docker_safe_stealth(context: Any, payload: str) -> None:
    async def inject(page: Any) -> None:
        try:
            await page.evaluate(payload)
        except Exception:
            pass

    async def on_frame_navigated(frame: Any) -> None:
        try:
            if frame == frame.page.main_frame:
                await inject(frame.page)
        except Exception:
            pass

    async def on_new_page(page: Any) -> None:
        page.on("framenavigated", on_frame_navigated)
        await inject(page)

    for page in context.pages:
        page.on("framenavigated", on_frame_navigated)
        await inject(page)
    context.on("page", on_new_page)
