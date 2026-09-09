"""失败取证快照。

契约见 `../../../AliECS/docs/constraints/browser-capture-evidence.md`（三个浏览器
抓取任务的共同下限）。这里除了原有的 page.html / screenshot.png / selector_report.json，
还写两样：

- `capture.json`：字段与 `ai-quota-monitor` 的 `captures` 表同名，好让三条链路能用同一套
  问题问下去（`status` / `confidence` / `captured_at`(UTC) / `url` / `screenshot_sha256`）。
- `index.jsonl`：一次一行的索引。⚠️ 没有索引时，「不登设备就能判断失败在哪一步」是做不到的
  ——`selector_report.json` 散在 59 个目录里，只能逐个翻。
"""
from __future__ import annotations

import hashlib
import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.browser import selectors
from src.browser.detector import any_selector_found
from src.config import get_settings

log = logging.getLogger(__name__)

INDEX_NAME = "index.jsonl"


async def save_debug_dump(page: Any | None, error: BaseException | str) -> str | None:
    if page is None:
        return None

    settings = get_settings()
    captured_at = datetime.now(timezone.utc)
    debug_dir = settings.debug_dir / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    debug_dir.mkdir(parents=True, exist_ok=True)

    shot_error = ""
    try:
        await page.screenshot(path=str(debug_dir / "screenshot.png"), full_page=True)
    except Exception as exc:
        # 取证失败不能替换掉它要记录的那个错误，只能并进去。
        shot_error = f"screenshot: {type(exc).__name__}: {str(exc)[:200]}"
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

    _write_capture_record(debug_dir, report, error, captured_at, shot_error)
    _prune_expired(settings.debug_dir, settings.debug_retention_days, captured_at)

    return _display_path(debug_dir)


def _write_capture_record(debug_dir: Path, report: dict[str, Any], error: BaseException | str,
                          captured_at: datetime, shot_error: str) -> None:
    """落一条与 quota-monitor `captures` 同名字段的结构化记录，并追加进索引。

    ``confidence`` 是「这一页还剩几个预期结构」——全找不到多半是页面被打成了错误边界
    （2026-09-09 的 /project 空页就是这个形状），找得到大半说明失败在别处。
    """
    groups = [key for key in report if isinstance(report.get(key), bool)]
    found = [key for key in groups if report[key]]
    confidence = round(len(found) / len(groups), 3) if groups else 0.0
    code = getattr(error, "code", None)
    error_text = getattr(error, "message", None) or str(error)
    if shot_error:
        error_text = f"{error_text} | {shot_error}" if error_text else shot_error
    digest = ""
    shot = debug_dir / "screenshot.png"
    try:
        if shot.is_file():
            digest = hashlib.sha256(shot.read_bytes()).hexdigest()
    except OSError:
        digest = ""
    record = {
        "task": "chat",
        "captured_at": captured_at.isoformat(),
        "url": report.get("current_url", ""),
        "title": report.get("title", ""),
        # 下游判据用结构化的 error_code，不要匹配人类可读文案。
        "error_code": getattr(code, "value", "" if code is None else str(code)),
        "status": "schema_changed" if confidence == 0.0 else "healthy",
        "confidence": confidence,
        "fields_json": json.dumps({key: report[key] for key in groups}, ensure_ascii=False),
        "error": error_text[:1000],
        "screenshot_path": "screenshot.png" if digest else "",
        "screenshot_sha256": digest,
        "debug_dir": debug_dir.name,
    }
    try:
        (debug_dir / "capture.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        with (debug_dir.parent / INDEX_NAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("could not write capture record in %s: %s", debug_dir, exc)


def _prune_expired(root: Path, retention_days: int, now: datetime) -> int:
    """删掉超过保留期的快照目录，返回删掉的目录数。

    ⚠️ 2026-09-09 之前这里**没有保留期**，设备上积到 59 个目录且没有任何机制会提醒。
    按目录名里的日期判，不按 mtime——回看一个旧快照会改 mtime，按 mtime 判会让它续命。
    索引行同样按日期滤掉，否则索引会指向已经删掉的目录。
    """
    if retention_days <= 0 or not root.is_dir():
        return 0
    cutoff = (now.timestamp() - retention_days * 86400)
    removed = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir() or len(child.name) < 10:
            continue
        try:
            stamp = datetime.strptime(child.name[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if stamp.timestamp() >= cutoff:
            continue
        try:
            for item in child.iterdir():
                item.unlink()
            child.rmdir()
            removed += 1
        except OSError:
            continue
    if removed:
        _prune_index(root / INDEX_NAME, root)
    return removed


def _prune_index(index: Path, root: Path) -> None:
    """只留下目录还在的索引行。"""
    try:
        if not index.is_file():
            return
        kept = []
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                name = json.loads(line).get("debug_dir", "")
            except ValueError:
                continue
            if name and (root / name).is_dir():
                kept.append(line)
        index.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError as exc:
        log.warning("could not prune debug index: %s", exc)


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
