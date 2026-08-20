from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)


# Generated images can arrive as a filename pill ("重新发我" replies reference the
# earlier picture as a file instead of re-rendering an <img>), so image extensions
# are first-class download targets too — they're delivered as MEDIA, not FILE.
IMAGE_FILE_EXTENSIONS = frozenset({
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
})
# Everything ChatGPT generates (sandbox / backend-api files — the origin gate in
# _is_chatgpt_generated_href) is downloadable by default. Only formats that
# execute on double-click are refused, so a forwarded file can't become a
# ready-to-run payload in the chat. External third-party links are still never
# auto-downloaded — that boundary is the origin gate, not this list.
BLOCKED_FILE_EXTENSIONS = frozenset({
    ".apk",
    ".bat",
    ".cmd",
    ".com",
    ".dmg",
    ".exe",
    ".jar",
    ".msi",
    ".pif",
    ".scr",
    ".vbs",
})
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
# Clicking a generated-document pill makes ChatGPT's backend materialise the file
# before the browser sees a download event, which can take far longer than the
# 15s this used to allow (2026-07-27: docx replies delivered a bare filename
# because every attempt timed out). Images are unaffected — they keep the short
# wait because their pill opens a preview instead of downloading at all.
DOCUMENT_DOWNLOAD_TIMEOUT_MS = 60000
# ...but the common case is the pill opening the preview flyout, where no download
# event EVER fires, so waiting the full budget burned 60s of the reply's deadline
# for nothing (2026-07-28: a docx turn spent 60 of its 224s here). Give the direct
# download a short first look; if the flyout is up, switch to it immediately, and
# if it isn't, keep waiting out the rest of the budget for a genuinely slow file.
DOCUMENT_PILL_DOWNLOAD_TIMEOUT_MS = 10000
# Clicking the pill and looking once is not enough. 2026-08-19 probe on an idle
# page: the layer is up 0.36s after the click. In production, on the frame the
# answer completes, TWO clicks in a row opened nothing — the thread was still
# swapping the turn's `blob:` preview for its `backend-api/estuary/…` URL, so the
# click kept landing on nodes React was replacing. So: poll for the layer, and if
# it does not show up, click again. Cheap either way — the happy path exits on the
# first poll.
PREVIEW_LAYER_WAIT_SECONDS = 3.0
PREVIEW_LAYER_CLICK_ATTEMPTS = 3
PREVIEW_LAYER_POLL_SECONDS = 0.25
# ...and clicking more is not the answer either. 2026-08-19 production: three
# clicks over 13s all reported "no layer", we gave up at 23s — and a dump taken
# afterwards found the layer wide open (1365×646) with its 484×484 image loaded.
# The layer does not fail to open, it opens LATE: it waits for the full-size
# `estuary` image, which this device pulls through the proxy. That is the same
# reason the document path was given a 60s budget in 2026-07-27, and exactly what
# 2026-08-17 took away from prose-labelled image pills by routing them to a 4s
# "short look". So keep clicking a few times, then simply keep waiting.
PREVIEW_LAYER_TOTAL_BUDGET_SECONDS = 45.0
# The layer can also arrive after we have given up (2026-08-19: the user found the
# page parked on the full-screen preview). Take one late look — it both rescues
# the picture and keeps a stray layer from covering the next turn.
PREVIEW_LATE_LAYER_GRACE_SECONDS = 2.0
# The in-page fetch of the layer's image can come back empty (2026-08-19: src
# found, bytes=0, no reason logged). It goes through this device's proxy, so give
# it another go before writing the reply off.
PREVIEW_FETCH_ATTEMPTS = 3
PREVIEW_FETCH_RETRY_SECONDS = 1.5
# The document preview flyout ChatGPT opens when a generated-file pill is clicked.
# Its own download control is what actually produces the file. Both the container
# testid and the localized labels are matched — the UI language follows the
# account, and only the label differs between them.
_PREVIEW_FLYOUT_CONTAINERS = (
    "[data-testid='stage-thread-flyout']",
    "[data-testid='screen-threadFlyOut']",
    # 2026-08-17: an image produced by the code tool opens THIS layer instead —
    # `modal-lightbox-new` (also role=dialog), whose controls are an aria-label
    # 'Close' and two unlabelled buttons reading Save / Share. It carries no
    # aria-label='Download' and no <a download>, so the old flyout handling saw
    # "no preview open", waited out the whole 60s document budget and delivered
    # the reply with no picture at all (68s burned, user got text only).
    "[data-testid='modal-lightbox-new']",
)
# The file card's own download control. Localized labels follow the account's UI
# language; the English one is what this fleet's account renders.
FILE_CARD_DOWNLOAD_BUTTON = (
    "button[aria-label='Download file'], "
    "button[aria-label='下载文件'], "
    "button[aria-label='下載檔案']"
)
# It downloads promptly once ChatGPT has materialised the file (4.96s measured for
# a 1MB PDF); this is headroom, not an expected wait.
CARD_DOWNLOAD_TIMEOUT_MS = 45000
_PREVIEW_DOWNLOAD_LABELS = ("Download", "下载")
PREVIEW_DOWNLOAD_BUTTON = ", ".join(
    f"{container} button[aria-label='{label}']"
    for container in _PREVIEW_FLYOUT_CONTAINERS
    for label in _PREVIEW_DOWNLOAD_LABELS
)
_PREVIEW_CLOSE_CONTROLS = ("[data-testid='close-button']", "button[aria-label='Close']")
PREVIEW_CLOSE_BUTTON = ", ".join(
    f"{container} {control}"
    for container in _PREVIEW_FLYOUT_CONTAINERS
    for control in _PREVIEW_CLOSE_CONTROLS
)


@dataclass(frozen=True)
class DownloadTarget:
    kind: str
    filename: str
    href: str | None = None
    # Position among the page's file-card download controls, when this target came
    # from a card rather than from a prose link (see detector._FILE_CARD_SCAN_JS).
    control_index: int | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.href or ''}:{self.filename}"


@dataclass(frozen=True)
class DownloadedFile:
    filename: str
    content_type: str
    data: bytes


def parse_download_targets(raw_targets: object) -> list[DownloadTarget]:
    if not isinstance(raw_targets, list):
        return []
    out: list[DownloadTarget] = []
    seen: set[str] = set()
    for raw in raw_targets:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        href = _clean_optional(raw.get("href"))
        filename = _target_filename(raw)
        if not filename:
            continue
        if href:
            # Link targets: anything ChatGPT generated (origin gate) except
            # executable formats.
            if not _is_chatgpt_generated_href(href) or _is_blocked_extension(filename):
                continue
        else:
            # Button targets are labelled with either a filename pill or a
            # localized action ("下载 PDF 扫描件"). Accept on either signal — a
            # bare UI label (copy, reasoning toggle) matches neither, so it's
            # never clicked. The authoritative filename and extension come from
            # the download event (see _download_button).
            if kind != "button":
                continue
            looks_like_file = _has_file_extension(filename) and not _is_blocked_extension(filename)
            if not (looks_like_file or _is_download_intent(filename)):
                continue
        target = DownloadTarget(kind=kind or ("link" if href else "button"), filename=filename, href=href)
        if target.key in seen:
            continue
        seen.add(target.key)
        out.append(target)
    return out


async def download_chatgpt_file(page: object, target: DownloadTarget) -> DownloadedFile | None:
    if target.control_index is not None:
        file = await _download_via_card_control(page, target)
        if file is not None:
            return file
    if target.href:
        return await _download_link(page, target)
    return await _download_button(page, target)


async def _download_via_card_control(page: object, target: DownloadTarget) -> DownloadedFile | None:
    """Click a file card's own download button — the only control that downloads.

    2026-08-19, measured on the live page: this fires a real download event in
    ~5s carrying the true filename, and leaves the URL untouched. Everything else
    we tried this month opens a preview (the prose link) or navigates the tab away
    from the conversation (the lightbox's Save)."""
    try:
        control = page.locator(FILE_CARD_DOWNLOAD_BUTTON).nth(target.control_index)
        async with page.expect_download(timeout=CARD_DOWNLOAD_TIMEOUT_MS) as download_info:
            # Dispatch the click in-page rather than clicking for real. The control
            # is rendered with `pointer-events: none` until its card is hovered
            # (2026-08-19 measured: visible=True, opacity=1, box 36×36, but
            # pointerEvents='none'), so a mouse click can never land on it —
            # Playwright waits out its "receives events" check and gives up, which
            # is exactly the 5s Locator.click timeout production hit. hover() fails
            # for the same reason, and force=True would send a real click that
            # passes THROUGH to whatever sits underneath. Dispatching downloads the
            # file: measured 8.77s with the correct filename.
            await control.evaluate("el => el.click()")
        download = await download_info.value
    except Exception as exc:
        log.warning(
            "file card download control failed: %s: %s (file=%r index=%s)",
            type(exc).__name__,
            str(exc).splitlines()[0][:160] if str(exc) else "",
            target.filename,
            target.control_index,
        )
        return None
    return await _read_download(download, target)


async def _download_link(page: object, target: DownloadTarget) -> DownloadedFile | None:
    try:
        payload = await page.evaluate(_FETCH_DOWNLOAD_B64_JS, target.href)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    b64 = payload.get("data")
    if not isinstance(b64, str) or not b64:
        return None
    try:
        data = base64.b64decode(b64)
    except Exception:
        return None
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        return None
    filename = _safe_filename(str(payload.get("filename") or target.filename)) or target.filename
    content_type = str(payload.get("contentType") or _guess_content_type(filename))
    return DownloadedFile(filename=filename, content_type=content_type, data=data)


# Controls that live OUTSIDE any conversation turn — which is exactly where a
# preview overlay renders. When a file pill opens a preview instead of firing a
# download, this shows whether the overlay carries its own download control.
_OVERLAY_CONTROLS_JS = """
() => {
  const out = [];
  for (const node of document.querySelectorAll("button, [role='button'], a[href], [data-testid]")) {
    if (node.closest("[data-testid^='conversation-turn']")) continue;
    // The sidebar is always outside the turns and would fill the whole budget.
    if (node.closest("nav")) continue;
    if ((node.getAttribute("class") || "").includes("__menu-item")) continue;
    const text = (node.innerText || node.textContent || "").trim();
    const label = node.getAttribute("aria-label") || "";
    if (!text && !label) continue;
    out.push({
      tag: node.tagName,
      cls: (node.getAttribute("class") || "").slice(0, 80),
      testid: node.getAttribute("data-testid") || "",
      label: label.slice(0, 60),
      href: (node.getAttribute("href") || "").slice(0, 100),
      text: text.slice(0, 60),
    });
  }
  return out.slice(0, 50);
}
"""


async def _overlay_controls_debug(page: object) -> list[dict[str, str]]:
    """Diagnostics only — reads the DOM, never clicks."""
    try:
        raw = await page.evaluate(_OVERLAY_CONTROLS_JS)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


async def _download_button(page: object, target: DownloadTarget) -> DownloadedFile | None:
    # Only a KNOWN document extension earns the long backend-materialisation
    # budget. A pill labelled in prose carries no extension at all ("下载 800×800
    # 图片", 2026-08-17) and used to fall through to the document path: 10s for the
    # click + 50s waiting out the rest of the budget + 5s on a download control
    # that layer does not have = 68s spent to deliver nothing. Images and
    # extension-less pills both open a preview, so they take the short look.
    suffix = Path(target.filename).suffix.lower()
    is_document = bool(suffix) and suffix not in IMAGE_FILE_EXTENSIONS
    try:
        async with page.expect_download(
            timeout=DOCUMENT_PILL_DOWNLOAD_TIMEOUT_MS if is_document else 4000
        ) as download_info:
            await page.locator("button").filter(has_text=target.filename).first.click(timeout=5000)
        download = await download_info.value
    except Exception as exc:
        log.warning(
            "file pill click did not produce a download: %s: %s (file=%r)",
            type(exc).__name__,
            str(exc).splitlines()[0][:200] if str(exc) else "",
            target.filename,
        )
        # The pill opened a preview layer (document flyout since 2026-07-27,
        # image lightbox since 2026-08-17). Whatever the label said, the file is
        # in front of us: use the layer's own download control, else grab the
        # picture it is displaying.
        # 2026-08-18/19: the click can land on nothing. detector returns the
        # instant the answer is complete while the thread is still re-rendering,
        # so the click hits a node React is about to replace — no download AND no
        # layer. The old code fell straight through to the image fallback, which
        # polled an unopened layer for 5s and delivered a reply with no picture.
        # Keep clicking until the layer is actually there.
        if await _open_preview_layer(page, target):
            return await _recover_from_preview(page, target, allow_image_fallback=not is_document)
        # An image pill can also fire the overlay slightly late — _capture_preview_image
        # polls for it (observed 2026-07-18).
        if not is_document:
            return await _capture_preview_image_with_late_retry(page, target)
        # No preview: this pill is a direct download that is merely slow, so spend
        # the rest of the original budget on the event we already armed the click for.
        pending = await _await_pending_download(
            page, DOCUMENT_DOWNLOAD_TIMEOUT_MS - DOCUMENT_PILL_DOWNLOAD_TIMEOUT_MS
        )
        if pending is not None:
            return await _read_download(pending, target)
        return await _recover_from_preview(page, target, allow_image_fallback=not is_document)
    return await _read_download(download, target)


async def _open_preview_layer(page: object, target: DownloadTarget) -> bool:
    """Wait for the pill's preview layer, re-clicking the pill until it shows up.

    The caller has already clicked once, so the first pass only polls."""
    started = time.monotonic()
    deadline = started + PREVIEW_LAYER_TOTAL_BUDGET_SECONDS
    extra_clicks = 0
    next_click_at = started + PREVIEW_LAYER_WAIT_SECONDS
    while time.monotonic() < deadline:
        if await _preview_layer_present(page):
            log.info(
                "preview layer opened after %.1fs and %d extra click(s) (file=%r)",
                time.monotonic() - started,
                extra_clicks,
                target.filename,
            )
            return True
        # A few re-clicks cover a click that landed on nothing; after that the
        # layer is simply still loading its image, and clicking again won't help.
        if extra_clicks + 1 < PREVIEW_LAYER_CLICK_ATTEMPTS and time.monotonic() >= next_click_at:
            await _click_pill_again(page, target)
            extra_clicks += 1
            next_click_at = time.monotonic() + PREVIEW_LAYER_WAIT_SECONDS
        await asyncio.sleep(PREVIEW_LAYER_POLL_SECONDS)
    log.warning(
        "preview layer never opened in %.1fs after %d extra click(s) (file=%r)",
        time.monotonic() - started,
        extra_clicks,
        target.filename,
    )
    return False


async def _click_pill_again(page: object, target: DownloadTarget) -> None:
    """Click a file pill again after the previous click opened nothing.

    No download is armed for this attempt: for a document the caller still spends
    its remaining budget on `_await_pending_download`, which catches a download
    event fired by this click too, and for an image the preview layer is what we
    are after anyway."""
    try:
        await page.locator("button").filter(has_text=target.filename).first.click(timeout=5000)
    except Exception as exc:
        log.info(
            "file pill re-click failed: %s (file=%r)",
            type(exc).__name__,
            target.filename,
        )
        return
    log.info("file pill re-clicked after the previous click opened no preview (file=%r)", target.filename)


async def _capture_preview_image_with_late_retry(
    page: object, target: DownloadTarget
) -> DownloadedFile | None:
    """Grab the picture, then take one late look for a layer that arrived after
    we stopped waiting — 2026-08-19 the layer showed up seconds later and stayed
    on screen, so the user found the page parked on the full-screen preview."""
    captured = await _capture_preview_image(page, target)
    if captured is not None:
        return captured
    await asyncio.sleep(PREVIEW_LATE_LAYER_GRACE_SECONDS)
    if not await _preview_layer_present(page):
        return None
    log.warning("preview layer arrived late; capturing after the fact (file=%r)", target.filename)
    return await _capture_preview_image(page, target)


async def _recover_from_preview(
    page: object, target: DownloadTarget, *, allow_image_fallback: bool
) -> DownloadedFile | None:
    """Get the file out of an open preview layer, then always close the layer.

    Order matters: the layer's own download control is authoritative (it yields
    the real document), but the image lightbox has none — its Save/Share buttons
    carry no aria-label, and the layer already renders the full-size backend image.

    ⛔ Never click Save. 2026-08-18 measurement: it fires NO download event, it
    NAVIGATES the tab to the raw file URL (backend-api/estuary/content?id=…), so
    the conversation is gone and every following turn on that lane is answering
    against an image document. Grab the rendered image instead.

    The image fallback is refused for a KNOWN document extension: a PDF preview
    also renders as a backend image, and capturing it would ship page one as a
    picture instead of the file. Close the layer either way — a layer left open
    covers the thread and wedges every following turn."""
    try:
        file = await _click_preview_download_control(page, target)
        if file is not None:
            return file
        if not allow_image_fallback:
            return None
        return await _capture_preview_image(page, target, close_layer=False)
    finally:
        await _close_preview_flyout(page)


_PREVIEW_LAYER_PRESENT_JS = """
(selectors) => {  // preview-layer-present
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (r.width >= 1 && r.height >= 1) return true;
  }
  return false;
}
"""


async def _preview_layer_present(page: object) -> bool:
    """Same question as _preview_flyout_visible, asked in one DOM round-trip.

    The locator version costs a 1s timeout per container when nothing is open,
    which is far too slow to poll with."""
    try:
        return bool(await page.evaluate(_PREVIEW_LAYER_PRESENT_JS, list(_PREVIEW_FLYOUT_CONTAINERS)))
    except Exception:
        return False


async def _preview_flyout_visible(page: object) -> bool:
    """Is ChatGPT's document preview flyout on screen? Decides whether a pill click
    that produced no download opened the preview (use its own control) or is just a
    slow direct download (keep waiting)."""
    for container in _PREVIEW_FLYOUT_CONTAINERS:
        try:
            if await page.locator(container).first.is_visible(timeout=1000):
                return True
        except Exception:
            continue
    return False


async def _await_pending_download(page: object, timeout_ms: int) -> object | None:
    """Wait out the remaining download budget after the short first look expired.
    The click already happened, so a late download event still lands here."""
    if timeout_ms <= 0:
        return None
    try:
        return await page.wait_for_event("download", timeout=timeout_ms)
    except Exception:
        return None


async def _click_preview_download_control(
    page: object, target: DownloadTarget
) -> DownloadedFile | None:
    """Use the preview layer's own Download control if it has one.

    Clicking a generated-document pill opens ChatGPT's document preview instead of
    downloading, so the click resolves but no download event ever fires. Closing
    the layer is the caller's job (_recover_from_preview) — the image fallback
    needs the layer still on screen.

    2026-08-19: check the control EXISTS first. The image lightbox has none, and
    arming expect_download for it burned the full 60s document budget on every
    single image reply (measured 67.09s total for a turn that then failed)."""
    try:
        if await page.locator(PREVIEW_DOWNLOAD_BUTTON).count() == 0:
            log.info(
                "preview layer has no download control; going straight for the image (file=%r)",
                target.filename,
            )
            return None
    except Exception:
        pass
    try:
        async with page.expect_download(timeout=DOCUMENT_DOWNLOAD_TIMEOUT_MS) as download_info:
            await page.locator(PREVIEW_DOWNLOAD_BUTTON).first.click(timeout=5000)
        download = await download_info.value
    except Exception as exc:
        log.warning(
            "preview layer download control unusable: %s: %s (file=%r) overlay=%s",
            type(exc).__name__,
            str(exc).splitlines()[0][:200] if str(exc) else "",
            target.filename,
            await _overlay_controls_debug(page),
        )
        return None
    return await _read_download(download, target)


async def _close_preview_flyout(page: object) -> None:
    """Escape does NOT dismiss this flyout (verified 2026-07-27 — the layer stayed
    at width 751 through repeated Escapes, wedging every following turn with
    RESPONSE_TIMEOUT). It carries its own close control; the keypress is kept only
    as a fallback for a reshaped UI."""
    try:
        await page.locator(PREVIEW_CLOSE_BUTTON).first.click(timeout=3000)
        return
    except Exception:
        pass
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


# Presence of the container is not enough — it stays in the DOM after the flyout
# closes. Only a rendered box (non-zero width) means it is actually covering the
# thread. Returns the flyout's own controls too, so a close affordance can be
# found when Escape doesn't dismiss it.
_FLYOUT_STATE_JS = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return null;
  const controls = [];
  for (const node of el.querySelectorAll("button, [role='button']")) {
    controls.push({
      testid: node.getAttribute("data-testid") || "",
      label: (node.getAttribute("aria-label") || "").slice(0, 40),
      text: (node.innerText || node.textContent || "").trim().slice(0, 30),
    });
  }
  return {width: Math.round(rect.width), controls: controls.slice(0, 20)};
}
"""


async def dismiss_stale_preview_flyout(page: object) -> bool:
    """Close a document preview flyout left open from an earlier turn.

    A flyout that outlives its turn covers the thread and the next reply never
    reaches a completion signal — the turn dies with RESPONSE_TIMEOUT even though
    the message sent fine. The download paths dismiss their own flyout, but a turn
    that fails before reaching them (or anything opened by hand in noVNC) would
    otherwise wedge every following request, so each turn starts by clearing one.
    """
    state = None
    for container in _PREVIEW_FLYOUT_CONTAINERS:
        try:
            state = await page.evaluate(_FLYOUT_STATE_JS, container)
        except Exception:
            return False
        if state:
            break
    if not state:
        return False
    log.warning("dismissing a stale document preview flyout: %s", state)
    await _close_preview_flyout(page)
    return True


async def _read_download(download: object, target: DownloadTarget) -> DownloadedFile | None:
    try:
        path = await download.path()
    except Exception:
        return None
    if not path:
        log.warning("download event fired but produced no path (file=%r)", target.filename)
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        return None
    filename = _safe_filename(getattr(download, "suggested_filename", "") or target.filename) or target.filename
    if _is_blocked_extension(filename):
        # The real download decides the type; never forward an executable format
        # even if the button label looked like a harmless download.
        return None
    return DownloadedFile(filename=filename, content_type=_guess_content_type(filename), data=data)


# Take the picture from INSIDE the preview layer, using the very same selectors
# that decided the layer is open - so "the layer opened" and "found the image"
# can never disagree.
#
# It used to scan the whole page for an image outside any conversation turn that
# measured >= 300x300. That size gate stood in for "the big one in the layer",
# and 2026-08-20 disproved it: a 400x800 portrait renders 242x484 inside the
# layer, so the width gate dropped it and the turn shipped with no picture even
# though the layer had opened in 0.0s. Size never belonged in this decision - the
# in-chat image is not a thumbnail either, its src points at the same
# full-resolution file and only CSS shrinks it.
_PREVIEW_IMAGE_SRC_JS = """
(containers) => {
  for (const sel of containers) {
    for (const layer of document.querySelectorAll(sel)) {
      const imgs = [...layer.querySelectorAll("img")].filter((im) =>
        /backend-api\\/(estuary|files)\\/|oaiusercontent/.test(im.currentSrc || im.src || "")
      );
      // Normally the layer holds exactly one image; sort by rendered area so a
      // future icon-sized <img> in the layer chrome cannot win over the picture.
      imgs.sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight);
      if (imgs.length) return imgs[0].currentSrc || imgs[0].src;
    }
  }
  return null;
}
"""
# Diagnostics for the near miss: the layer is open and holds an image, but every
# candidate is still a `blob:` URL (2026-08-19 caught the frame where the page
# swaps its blob preview for the estuary original). Not accepted as a source yet
# - a blob may be a low-resolution placeholder and shipping a blurry picture is
# worse than failing - but logged so that call can rest on evidence.
_PREVIEW_BLOB_ONLY_JS = """
(containers) => {
  for (const sel of containers) {
    for (const layer of document.querySelectorAll(sel)) {
      const imgs = [...layer.querySelectorAll("img")];
      if (imgs.length && imgs.every((im) => (im.currentSrc || im.src || "").startsWith("blob:"))) {
        return imgs.length;
      }
    }
  }
  return 0;
}
"""
# Why did the src probe come up empty? Report every image on the page with the
# two facts the probe filters on (size and whether it sits inside a turn), so a
# failure says "layer never opened" (no candidate outside a turn) apart from
# "layer opened but the picture was filtered out".
_PREVIEW_IMAGE_CANDIDATES_JS = """
() => {
  const out = [];
  for (const im of document.querySelectorAll("img")) {
    out.push({
      src: (im.currentSrc || im.src || "").slice(0, 60),
      w: im.clientWidth,
      h: im.clientHeight,
      inTurn: !!im.closest("[data-testid^='conversation-turn']"),
    });
  }
  return out.slice(0, 8);
}
"""
# In-page fetch so the logged-in session cookies apply (estuary URLs need them).
_FETCH_PREVIEW_B64_JS = """
async (src) => {
  try {
    const res = await fetch(src, { credentials: "include" });
    if (!res.ok) return "!http " + res.status;
    const bytes = new Uint8Array(await res.arrayBuffer());
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  } catch (e) {
    return "!err " + (e && e.name ? e.name : "unknown");
  }
}
"""


async def _preview_image_candidates_debug(page: object) -> list[dict[str, object]]:
    """Diagnostics only — reads the DOM, never clicks."""
    try:
        raw = await page.evaluate(_PREVIEW_IMAGE_CANDIDATES_JS)
    except Exception:
        return []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


async def _preview_blob_only_count(page: object) -> int:
    """Diagnostics only - how many images the open layer holds when every one of
    them is still a blob: URL."""
    try:
        return int(await page.evaluate(_PREVIEW_BLOB_ONLY_JS, list(_PREVIEW_FLYOUT_CONTAINERS)) or 0)
    except Exception:
        return 0


async def _fetch_via_context_request(page: object, src: str) -> tuple[bytes | None, str | None]:
    """Fetch the preview image through the browser context's request API.

    Backs up the in-page fetch: same cookies, but no page is involved, so a
    broken page context cannot take it down with it. Returns (data, error)."""
    try:
        context = page.context
        response = await context.request.get(src)
    except Exception as exc:
        return None, type(exc).__name__
    try:
        status = response.status
        if status >= 400:
            return None, f"http {status}"
        data = await response.body()
    except Exception as exc:
        return None, f"body:{type(exc).__name__}"
    return (data or None), (None if data else "empty")


async def _capture_preview_image(
    page: object, target: DownloadTarget, *, close_layer: bool = True
) -> DownloadedFile | None:
    """Grab the image shown by the file pill's preview overlay.

    `close_layer` is False when the caller owns the layer's lifetime; note that
    Escape alone does NOT dismiss the document flyout (2026-07-27), so closing
    goes through _close_preview_flyout's control-first path."""
    src = None
    for _ in range(10):  # the overlay renders async after the click
        try:
            src = await page.evaluate(_PREVIEW_IMAGE_SRC_JS, list(_PREVIEW_FLYOUT_CONTAINERS))
        except Exception:
            src = None
        if src:
            break
        await asyncio.sleep(0.5)
    # Collect the candidates BEFORE closing the layer — after the close there is
    # nothing left to look at. This path used to fail with no log line at all,
    # which is why the 2026-08-18 loss could only be explained by replaying it
    # against the live page.
    candidates = [] if src else await _preview_image_candidates_debug(page)
    if not src:
        blob_only = await _preview_blob_only_count(page)
        if blob_only:
            log.warning(
                "preview layer holds %d image(s) but all of them are still blob: URLs (file=%r)",
                blob_only,
                target.filename,
            )
    data = None
    fetch_error = None
    if src:
        for attempt in range(PREVIEW_FETCH_ATTEMPTS):
            try:
                b64 = await page.evaluate(_FETCH_PREVIEW_B64_JS, src)
            except Exception as exc:
                fetch_error, b64 = f"evaluate:{type(exc).__name__}", None
            if isinstance(b64, str) and b64.startswith("!"):
                # The page reported the failure itself (HTTP status or fetch error);
                # without this the only symptom was "bytes=0" and no reason at all.
                fetch_error, b64 = b64, None
            if b64:
                try:
                    data = base64.b64decode(b64)
                    fetch_error = None
                    break
                except Exception as exc:
                    fetch_error = f"b64:{type(exc).__name__}"
            if attempt + 1 < PREVIEW_FETCH_ATTEMPTS:
                await asyncio.sleep(PREVIEW_FETCH_RETRY_SECONDS)
    if src and not data:
        # Second, independent way to turn the URL into bytes: the browser
        # context's own request API. It carries the session cookies without
        # touching a page, so it survives what killed the in-page fetch on
        # 2026-08-19 11:05 (src was fine, `fetch=!err TypeError`, no bytes).
        data, api_error = await _fetch_via_context_request(page, src)
        if api_error:
            fetch_error = f"{fetch_error} | api:{api_error}"
    if close_layer:
        await _close_preview_flyout(page)
    if not data or len(data) < 1024 or len(data) > MAX_DOWNLOAD_BYTES:
        log.warning(
            "preview image capture failed: src=%r bytes=%d fetch=%s (file=%r) candidates=%s",
            (src or "")[:100] or None,
            len(data) if data else 0,
            fetch_error,
            target.filename,
            candidates,
        )
        return None
    # A prose-labelled pill has no extension, so the guessed type would be
    # application/octet-stream and the picture would be delivered as a file card
    # instead of being rendered inline. What we captured IS the preview image;
    # name it accordingly so the caller emits MEDIA.
    filename = target.filename
    content_type = _guess_content_type(filename)
    if not content_type.startswith("image/"):
        content_type = _sniff_image_type(data) or "image/png"
        filename = f"{Path(filename).stem or 'image'}{mimetypes.guess_extension(content_type) or '.png'}"
    return DownloadedFile(filename=filename, content_type=content_type, data=data)


def _sniff_image_type(data: bytes) -> str | None:
    """Identify the captured bytes by magic number — the pill's label cannot."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _target_filename(raw: dict[str, object]) -> str | None:
    for key in ("download", "filename", "text"):
        filename = _safe_filename(_clean_optional(raw.get(key)))
        if filename:
            return filename
    href = _clean_optional(raw.get("href"))
    if not href:
        return None
    parsed = urlparse(href)
    name = Path(unquote(parsed.path)).name
    return _safe_filename(name)


def _is_chatgpt_generated_href(href: str) -> bool:
    """ChatGPT-generated file URLs come in three known shapes:
    - legacy code-interpreter links: sandbox:/mnt/data/... (or bare /mnt/data/)
    - legacy file downloads: chatgpt.com/backend-api/files/<id>/download
    - the current file service (images AND documents, observed 2026-07-18):
      chatgpt.com/backend-api/estuary/content?id=file_...&ts=...&sig=...
    oaiusercontent.com is OpenAI's signed user-content CDN (same trust class).
    Anything else is a third-party link and is never auto-downloaded."""
    if href.startswith("sandbox:/mnt/data/") or href.startswith("/mnt/data/"):
        return True
    parsed = urlparse(href)
    path = parsed.path or href
    host = (parsed.netloc or "").lower()
    if host.endswith("oaiusercontent.com"):
        return True
    if host and not host.endswith("chatgpt.com"):
        return False
    if "/backend-api/files/" in path and path.rstrip("/").endswith("/download"):
        return True
    return "/backend-api/estuary/content" in path and "id=file" in (parsed.query or "")


# A real filename suffix (".pdf", ".json") — not a stray dot inside a sentence
# label ("v2.0 说明" must not read as a file).
_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def _has_file_extension(filename: str) -> bool:
    return bool(_FILE_EXTENSION_RE.search(filename.strip()))


def _is_blocked_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in BLOCKED_FILE_EXTENSIONS


# ChatGPT labels a generated-file button with a localized action, not a filename
# (e.g. "下载 PDF 扫描件", "Download the report"). Match download intent so we only
# click real download affordances and never pay an expect_download timeout on an
# unrelated inline button.
_DOWNLOAD_INTENT_RE = re.compile(
    r"下载|下載|导出|導出|另存|保存|download|export"
    r"|\b(?:pdf|word|excel|csv|pptx?|docx?|xlsx?|txt)\b"
    r"|文档|文檔|表格|文件|附件",
    re.IGNORECASE,
)


def _is_download_intent(label: str | None) -> bool:
    return bool(label and _DOWNLOAD_INTENT_RE.search(label))


def mentions_download_intent(text: str | None) -> bool:
    """Does this reply talk about a file the reader is meant to download?

    Same vocabulary as the pill matcher — used to decide whether it is worth
    waiting for a pill that has not rendered yet."""
    return _is_download_intent(text)


def _safe_filename(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = Path(value.replace("\\", "/")).name.strip().strip('"')
    if not cleaned or cleaned in {".", ".."}:
        return None
    return cleaned.replace("\r", "_").replace("\n", "_")


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


_FETCH_DOWNLOAD_B64_JS = """
async (href) => {
  try {
    const r = await fetch(href);
    if (!r.ok) return null;
    const len = Number(r.headers.get('content-length') || '0');
    if (len > 26214400) return null;
    const bytes = new Uint8Array(await r.arrayBuffer());
    if (bytes.length > 26214400) return null;
    let bin = '';
    const CH = 8192;
    for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    const cd = r.headers.get('content-disposition') || '';
    const m = cd.match(/filename\\*?=(?:UTF-8''|")?([^";]+)/i);
    return {
      data: btoa(bin),
      contentType: (r.headers.get('content-type') || '').split(';')[0] || '',
      filename: m ? decodeURIComponent(m[1].replace(/"/g, '')) : ''
    };
  } catch (e) {
    return null;
  }
}
"""
