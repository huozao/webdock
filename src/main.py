from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.routes_browser import router as browser_router
from src.api.routes_chat import router as chat_router
from src.api.routes_health import router as health_router
from src.browser.lane_scheduler import ChatLaneScheduler
from src.browser.manager import BrowserManager
from src.config import get_settings
from src.utils.errors import ErrorCode, error_response
from src.utils.logging import setup_logging


def create_app(*, start_browser: bool = True) -> FastAPI:
    setup_logging()
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.browser = BrowserManager()
        app.state.chat_scheduler = ChatLaneScheduler(max_concurrent_chats=settings.max_concurrent_chats)
        if start_browser and settings.attach_on_start:
            try:
                await app.state.browser.start()
            except Exception as exc:
                app.state.browser.last_error = str(exc)
        yield
        await app.state.browser.stop()

    app = FastAPI(title="webdock", version="0.1.0", lifespan=lifespan)
    app.state.browser = BrowserManager()
    app.state.chat_scheduler = ChatLaneScheduler(max_concurrent_chats=settings.max_concurrent_chats)

    @app.middleware("http")
    async def bearer_token_auth(request: Request, call_next):
        if request.url.path in {"/healthz", "/docs", "/redoc", "/openapi.json"}:
            return await call_next(request)
        if not settings.api_token:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        expected = f"Bearer {settings.api_token}"
        if auth_header != expected:
            return JSONResponse(
                status_code=401,
                content=error_response(
                    ErrorCode.AUTH_FAILED,
                    "Invalid or missing API token. Use Authorization: Bearer <API_TOKEN>.",
                ),
            )
        return await call_next(request)

    app.include_router(health_router)
    app.include_router(browser_router)
    app.include_router(chat_router)
    return app


app = create_app()
