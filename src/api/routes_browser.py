from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.utils.errors import ErrorCode, error_response

router = APIRouter()


@router.get("/browser-status", response_model=None)
async def browser_status(request: Request, attach: bool = False) -> dict[str, object] | JSONResponse:
    browser = request.app.state.browser
    if attach and not browser.started:
        attach_error = await _attach_browser(browser)
        if attach_error:
            return attach_error
    return await browser.status()


@router.post("/browser/attach", response_model=None)
async def browser_attach(request: Request) -> dict[str, object] | JSONResponse:
    browser = request.app.state.browser
    if not browser.started:
        attach_error = await _attach_browser(browser)
        if attach_error:
            return attach_error
    return await browser.status()


@router.post("/browser/detach")
async def browser_detach(request: Request) -> dict[str, object]:
    browser = request.app.state.browser
    await browser.detach()
    return await browser.status()


async def _attach_browser(browser) -> JSONResponse | None:
    try:
        await browser.start()
        browser.last_error = None
    except Exception as exc:
        browser.last_error = str(exc)
        return JSONResponse(
            status_code=503,
            content=error_response(
                ErrorCode.BROWSER_NOT_STARTED,
                "Cannot attach to Chrome CDP. Open noVNC, make sure Chrome is running, then retry.",
            ),
        )
    return None
