from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from patchright.async_api import async_playwright

from .core import event_key, record_event_once, weekly_reset_candidate

LOG = logging.getLogger("quota-monitor")
DATA_DIR = Path(os.getenv("QUOTA_DATA_DIR", "/app/quota_data"))
PROFILE_DIR = Path(os.getenv("QUOTA_PROFILE_DIR", "/app/quota_browser_data"))
ENABLE_FILE = DATA_DIR / "ATTACH_ENABLED"
DB_PATH = DATA_DIR / "quota.sqlite3"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
POLL_MIN = float(os.getenv("QUOTA_POLL_MINUTES_MIN", "20"))
POLL_MAX = float(os.getenv("QUOTA_POLL_MINUTES_MAX", "30"))
PAGE_SETTLE_SECONDS = float(os.getenv("QUOTA_PAGE_SETTLE_SECONDS", "5"))

PROVIDERS = {
    "codex": "https://chatgpt.com/codex/cloud/settings/usage",
    "claude": "https://claude.ai/settings/usage",
}

app = FastAPI(title="quota-monitor", version="0.1.0")
_task: asyncio.Task[None] | None = None
_stop = asyncio.Event()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS captures (
          id INTEGER PRIMARY KEY, provider TEXT NOT NULL, captured_at TEXT NOT NULL,
          url TEXT NOT NULL, text TEXT NOT NULL, fields_json TEXT NOT NULL,
          status TEXT NOT NULL, confidence REAL NOT NULL, screenshot_path TEXT,
          screenshot_sha256 TEXT, error TEXT, reset_key TEXT
        )"""
    )
    conn.commit()
    return conn


def _parse(provider: str, text: str) -> tuple[dict[str, Any], float, str]:
    """保守解析：页面结构变化时保留原文和截图，不把未知内容当成零额度。"""
    lowered = text.lower()
    if any(x in lowered for x in ("log in", "sign in", "登录", "登录后")):
        return {}, 0.0, "auth_required"
    if provider == "codex":
        fields: dict[str, Any] = {}
        five_hour = re.search(r"5\s*hour\s*usage\s*limit\s*([\d,.]+\s*%)\s*remaining", text, flags=re.I | re.S)
        weekly = re.search(r"weekly\s*usage\s*limit\s*([\d,.]+\s*%)\s*remaining", text, flags=re.I | re.S)
        credits = re.search(r"credits\s*remaining\s*([\d,.]+)", text, flags=re.I | re.S)
        reset = re.search(r"resets?\s+([^\n]{1,80})", text, flags=re.I)
        if five_hour:
            remaining = five_hour.group(1).strip()
            try:
                used = f"{100.0 - float(remaining.rstrip('%')):g}%"
            except ValueError:
                used = None
            fields.update({"remaining": remaining, "window": "5-hour", "limit": "100%"})
            if used is not None:
                fields["used"] = used
        if weekly:
            fields["weekly_remaining"] = weekly.group(1).strip()
            try:
                fields["weekly_used_percent"] = f"{100.0 - float(fields['weekly_remaining'].rstrip('%')):g}%"
            except ValueError:
                pass
        if credits:
            fields["credits_remaining"] = credits.group(1).strip()
        if reset:
            fields["reset_at"] = reset.group(1).strip()
            # 页面当前只在周额度块下展示一个明确的 Codex 重置时间。
            fields["weekly_reset_at"] = fields["reset_at"]
        return fields, min(1.0, 0.6 + 0.1 * len(fields)) if fields else 0.0, "healthy" if fields else "schema_changed"
    fields: dict[str, Any] = {}
    patterns = {
        "used": r"(?:used|已用)\s*[:：]?\s*([\d,.]+\s*%?)",
        "remaining": r"(?:remaining|left|剩余)\s*[:：]?\s*([\d,.]+\s*%?)",
        "reset_at": r"(?:resets?|reset|重置)\s*(?:at|in|时间)?\s*[:：]?\s*([^\n]{1,80})",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.I)
        if match:
            fields[key] = match.group(1).strip()
    confidence = min(1.0, 0.35 + 0.25 * len(fields))
    status = "healthy" if fields else "schema_changed"
    if provider == "codex" and "usage" in lowered and fields:
        confidence = min(1.0, confidence + 0.1)
    return fields, confidence, status


async def _notify(event: str, title: str, summary: str, *, screenshot_paths: list[Path] = None,
                  dedup_key: str = "") -> None:
    endpoint = os.getenv("NOTIFY_ENDPOINT", "").strip()
    token = os.getenv("NOTIFY_SOURCE_TOKEN", "").strip()
    if not endpoint or not token:
        return
    images: list[dict[str, str]] = []
    segments: list[dict[str, Any]] = [{"kind": "text", "text": summary}]
    for index, path in enumerate(screenshot_paths or []):
        try:
            raw = path.read_bytes()
            if len(raw) > 2 * 1024 * 1024:
                LOG.warning("notification image too large path=%s", path)
                continue
            ref = f"screen-{index}"
            images.append({"ref": ref, "caption": path.stem, "png_base64": __import__("base64").b64encode(raw).decode()})
            segments.append({"kind": "image", "image_ref": ref})
        except OSError:
            continue
    payload = {"source": "quota-monitor", "event": event, "level": "warn" if event == "quota.reset" else "info",
               "title": title, "summary": summary, "segments": segments, "images": images,
               "link": {"text": "查看额度历史", "url": "https://hydwang.xyz/console/quota/"},
               "dedup_key": dedup_key or f"quota:{event}:{int(time.time())}"}
    body = json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Notify-Source": "quota-monitor", "X-Notify-Token": token,
    })
    try:
        await asyncio.to_thread(lambda: urllib.request.urlopen(request, timeout=20).read())
    except Exception as exc:
        LOG.warning("notification failed event=%s reason=%s", event, type(exc).__name__)


async def _capture(page: Any, provider: str) -> dict[str, Any]:
    captured_at = _utc_now()
    error = ""
    status = "healthy"
    confidence = 0.0
    text = ""
    fields: dict[str, Any] = {}
    screenshot_path: Path | None = None
    try:
        await page.reload(wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(int(PAGE_SETTLE_SECONDS * 1000))
        text = await page.locator("body").inner_text(timeout=15_000)
        fields, confidence, status = _parse(provider, text)
        if provider == "claude":
            meters = await page.locator("[role=meter][aria-valuenow][aria-valuemax='100']").evaluate_all(
                "els => els.map(e => ({value:e.getAttribute('aria-valuenow'), text:e.getAttribute('aria-valuetext') || ''}))"
            )
            numeric_meters = [item for item in meters if re.fullmatch(r"\d+(?:\.\d+)?", str(item.get("value", "")))]
            if numeric_meters:
                session_used = float(numeric_meters[0]["value"])
                fields["session_used_percent"] = numeric_meters[0]["value"]
                fields["used"] = f"{session_used:g}%"
                fields["remaining"] = f"{100.0 - session_used:g}%"
                fields["unit"] = "%"
                fields["window"] = "5-hour session"
                if len(numeric_meters) > 1:
                    weekly_used = float(numeric_meters[1]["value"])
                    fields["weekly_used_percent"] = f"{weekly_used:g}%"
                    fields["weekly_remaining"] = f"{100.0 - weekly_used:g}%"
                resets = re.findall(r"resets?\s+([^\n]{1,80})", text, flags=re.I)
                if len(resets) > 1:
                    fields["weekly_reset_at"] = resets[1].strip()
                confidence = max(confidence, 0.9)
                status = "healthy"
        screenshot_path = SCREENSHOT_DIR / f"{provider}-{int(time.time())}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception as exc:  # 保留失败记录，不能静默丢失截图/错误
        error = type(exc).__name__ + ": " + str(exc)[:500]
        status = "network_error"
    digest = ""
    if screenshot_path and screenshot_path.exists():
        digest = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
    conn = _db()
    previous = conn.execute(
        "SELECT fields_json, status FROM captures WHERE provider=? ORDER BY id DESC LIMIT 1", (provider,)
    ).fetchone()
    reset_key = f"{provider}:{fields.get('reset_at','')}" if fields.get("reset_at") else None
    reset_detected = False
    if previous and status == "healthy" and previous["status"] == "healthy":
        try:
            old_fields = json.loads(previous["fields_json"] or "{}")
        except json.JSONDecodeError:
            old_fields = {}
        reset_detected = weekly_reset_candidate(
            {"status": status, "fields": fields},
            {"status": previous["status"], "fields": old_fields},
        )
    conn.execute(
        "INSERT INTO captures(provider,captured_at,url,text,fields_json,status,confidence,screenshot_path,screenshot_sha256,error,reset_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (provider, captured_at, str(getattr(page, "url", "")), text, json.dumps(fields, ensure_ascii=False), status,
         confidence, str(screenshot_path) if screenshot_path else None, digest, error, reset_key),
    )
    conn.commit()
    if reset_detected:
        reset_detected = record_event_once(
            conn, event_key(provider, {"weekly_reset_at": fields.get("weekly_reset_at"), "weekly_remaining": fields.get("weekly_remaining")}), provider, captured_at,
            {"provider": provider, "fields": fields, "screenshot_sha256": digest},
        )
    conn.close()
    LOG.info("capture provider=%s status=%s confidence=%.2f screenshot=%s", provider, status, confidence, bool(digest))
    result = {"provider": provider, "status": status, "fields": fields, "screenshot_path": screenshot_path,
              "captured_at": captured_at, "reset_detected": reset_detected}
    if reset_detected:
        weekly_summary = {key: fields.get(key) for key in ("weekly_remaining", "weekly_used_percent", "weekly_reset_at")}
        await _notify("quota.reset", f"{provider} 周额度已重置", json.dumps(weekly_summary, ensure_ascii=False),
                       screenshot_paths=[screenshot_path] if screenshot_path else [],
                       dedup_key=f"quota:weekly_reset:{provider}:{fields.get('weekly_reset_at','')}" )
    return result


async def _run() -> None:
    # 关键安全边界：在 ATTACH_ENABLED 出现前，浏览器存在但 Playwright/CDP 不接管。
    while not _stop.is_set():
        if not ENABLE_FILE.exists():
            await asyncio.sleep(5)
            continue
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9224")
                context = browser.contexts[0] if browser.contexts else None
                if context is None:
                    raise RuntimeError("quota Chrome has no browser context")
                pages = list(context.pages)
                captured: list[dict[str, Any]] = []
                for provider, url in PROVIDERS.items():
                    page = next((p for p in pages if provider in p.url.lower()), None)
                    if page is None:
                        page = await context.new_page()
                        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                        pages.append(page)
                    elif provider == "claude" and "#settings/usage" not in page.url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    elif provider == "codex" and "/codex/cloud/settings/" not in page.url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    captured.append(await _capture(page, provider))
                await _maybe_daily_report(captured)
                # CDP attach 的 browser.close() 会关闭用户仍需查看的 Chrome；
                # async with 退出时只结束 Playwright 连接，浏览器进程和手工登录态保持不动。
        except Exception as exc:
            LOG.warning("monitor cycle failed: %s: %s", type(exc).__name__, exc)
        delay = random.uniform(POLL_MIN, POLL_MAX) * 60
        await asyncio.sleep(delay)


_last_report_key = ""


async def _maybe_daily_report(captured: list[dict[str, Any]]) -> None:
    global _last_report_key
    slots = [item.strip() for item in os.getenv("QUOTA_REPORT_TIMES", "08:00,13:00,20:00").split(",")]
    now = datetime.now().astimezone()
    slot = None
    for item in slots:
        try:
            hour, minute = (int(value) for value in item.split(":", 1))
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (TypeError, ValueError):
            continue
        if now >= candidate:
            slot = item
    key = f"{now.date()}:{slot}" if slot else ""
    if not slot or key == _last_report_key:
        return
    _last_report_key = key
    paths = [item["screenshot_path"] for item in captured if item.get("screenshot_path")]
    summary = "\n".join(f"{item['provider']}: {item['status']} {item['fields']}" for item in captured)
    await _notify("quota.daily_report", "AI 额度日报", summary, screenshot_paths=paths,
                   dedup_key=f"quota:daily_report:{now.date()}:{slot}")


@app.on_event("startup")
async def startup() -> None:
    global _task
    _stop.clear()
    _task = asyncio.create_task(_run())


@app.on_event("shutdown")
async def shutdown() -> None:
    _stop.set()
    if _task:
        _task.cancel()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "attach_enabled": ENABLE_FILE.exists(), "db": str(DB_PATH)}


@app.post("/v1/monitor/enable")
def enable_monitor() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENABLE_FILE.touch()
    return {"ok": True, "attach_enabled": True}


@app.post("/v1/monitor/disable")
def disable_monitor() -> dict[str, Any]:
    ENABLE_FILE.unlink(missing_ok=True)
    return {"ok": True, "attach_enabled": False}


def _quota_public(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "used": fields.get("used"),
        "remaining": fields.get("remaining"),
        "weekly_used": fields.get("weekly_used_percent"),
        "weekly_remaining": fields.get("weekly_remaining"),
        "credits_remaining": fields.get("credits_remaining"),
        "unit": "%" if any("%" in str(v) for v in fields.values()) else "",
        "window": fields.get("window", ""),
        "reset_at": fields.get("reset_at"),
        "weekly_reset_at": fields.get("weekly_reset_at"),
    }


@app.get("/v1/quota/latest")
def latest() -> dict[str, Any]:
    conn = _db()
    rows = conn.execute("SELECT * FROM captures WHERE id IN (SELECT MAX(id) FROM captures GROUP BY provider)").fetchall()
    conn.close()
    providers: dict[str, Any] = {}
    for row in rows:
        fields = json.loads(row["fields_json"] or "{}")
        public = _quota_public(fields)
        providers[row["provider"]] = {
            "id": row["id"], "status": row["status"], "used": fields.get("used"),
            "remaining": fields.get("remaining"), "unit": "%" if any("%" in str(v) for v in fields.values()) else "",
            "window": fields.get("window", ""), "reset_at": fields.get("reset_at"),
            "screenshot_url": f"/console/quota/api/captures/{row['id']}/screenshot",
            "screenshot_captured_at": row["captured_at"], "confidence": row["confidence"],
            "text": row["text"], "error": row["error"],
        }
        providers[row["provider"]].update(public)
    return {"providers": providers, "generated_at": _utc_now()}


@app.get("/v1/quota/history")
def history(limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    conn = _db()
    rows = conn.execute("SELECT * FROM captures ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in PROVIDERS}
    for row in rows:
        fields = json.loads(row["fields_json"] or "{}")
        public = _quota_public(fields)
        result.setdefault(row["provider"], []).append({
            "id": row["id"], "captured_at": row["captured_at"], "status": row["status"],
            "used": fields.get("used"), "remaining": fields.get("remaining"),
            "unit": "%" if any("%" in str(v) for v in fields.values()) else "",
            "window": fields.get("window", ""), "reset_at": fields.get("reset_at"),
            "screenshot_url": f"/console/quota/api/captures/{row['id']}/screenshot",
            "confidence": row["confidence"], "error": row["error"],
        })
        result[row["provider"]][-1].update(public)
    return {"providers": result, "generated_at": _utc_now()}


@app.get("/v1/quota/captures/{capture_id}/screenshot")
@app.get("/console/quota/api/captures/{capture_id}/screenshot")
def screenshot(capture_id: int) -> FileResponse:
    conn = _db()
    row = conn.execute("SELECT screenshot_path FROM captures WHERE id=?", (capture_id,)).fetchone()
    conn.close()
    if not row or not row["screenshot_path"] or not Path(row["screenshot_path"]).is_file():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(row["screenshot_path"], media_type="image/png")
