from __future__ import annotations

import uvicorn

from src.config import get_settings
from src.utils.logging import build_uvicorn_log_config


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_config=build_uvicorn_log_config(settings.log_level),
    )


if __name__ == "__main__":
    main()
