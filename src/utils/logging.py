from __future__ import annotations

import logging
import sys
import time

from src.config import get_settings

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


class UtcIsoFormatter(logging.Formatter):
    """Render timestamps as unambiguous UTC ISO8601 with a Z suffix and
    milliseconds, e.g. ``2026-06-23T15:30:00.123Z`` — so every log line shows its
    timezone at a glance regardless of the container's local time."""

    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        base = time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created))
        return f"{base}.{int(record.msecs):03d}Z"


def setup_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(UtcIsoFormatter(LOG_FORMAT))
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )


def build_uvicorn_log_config(log_level: str = "INFO") -> dict:
    """uvicorn log config that renders both startup/error and access lines in the
    same UTC ISO8601-Z format as the app logs (uvicorn's default access line has no
    timestamp at all)."""
    from uvicorn.logging import AccessFormatter

    class UtcAccessFormatter(AccessFormatter):
        converter = time.gmtime

        def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
            base = time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created))
            return f"{base}.{int(record.msecs):03d}Z"

    level = log_level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"()": UtcIsoFormatter, "format": "%(asctime)s | %(levelname)s | %(message)s"},
            "access": {
                "()": UtcAccessFormatter,
                "format": '%(asctime)s | %(levelname)s | %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stdout"},
            "access": {"class": "logging.StreamHandler", "formatter": "access", "stream": "ext://sys.stdout"},
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": level, "propagate": False},
        },
    }
