from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from src.browser import selectors
from src.browser.detector import any_selector_found
from src.config import get_settings


async def save_debug_dump(page: Any | None, error: BaseException | str) -> str | None:
    if page is None:
        return None

    settings = get_settings()
    debug_dir = settings.debug_dir / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    debug_dir.mkdir(parents=True, exist_ok=True)

    try:
        await page.screenshot(path=str(debug_dir / "screenshot.png"), full_page=True)
    except Exception as exc:
        (debug_dir / "screenshot.error.txt").write_text(str(exc), encoding="utf-8")

    try:
        html = await page.content()
        (debug_dir / "page.html").write_text(html, encoding="utf-8")
    except Exception as exc:
        (debug_dir / "page.error.txt").write_text(str(exc), encoding="utf-8")

    try:
        (debug_dir / "current_url.txt").write_text(page.url or "", encoding="utf-8")
    except Exception:
        (debug_dir / "current_url.txt").write_text("", encoding="utf-8")

    report = await build_selector_report(page)
    (debug_dir / "selector_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if isinstance(error, BaseException):
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    else:
        trace = str(error)
    (debug_dir / "error_trace.txt").write_text(trace, encoding="utf-8")

    return _display_path(debug_dir)


async def build_selector_report(page: Any) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name, selector_list in selectors.SELECTOR_GROUPS.items():
        report[name] = await any_selector_found(page, selector_list)

    try:
        title = await page.title()
    except Exception:
        title = ""

    report["current_url"] = getattr(page, "url", "")
    report["title"] = title
    return report


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd())).replace("\\", "/")
    except ValueError:
        return str(path)
