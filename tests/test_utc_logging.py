from __future__ import annotations

import logging
import re

from src.utils.logging import UtcIsoFormatter

_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _record(epoch: float, msecs: int) -> logging.LogRecord:
    rec = logging.LogRecord("name", logging.INFO, "path", 1, "hello", None, None)
    rec.created = epoch
    rec.msecs = msecs
    return rec


def test_formatter_emits_iso8601_utc_with_z_suffix():
    fmt = UtcIsoFormatter("%(asctime)s | %(levelname)s | %(message)s")
    # 1750000000 == 2025-06-15T15:06:40Z (UTC, independent of host TZ).
    out = fmt.format(_record(1750000000.0, 123))
    ts = out.split(" | ", 1)[0]
    assert _ISO_Z_RE.match(ts), ts
    assert ts == "2025-06-15T15:06:40.123Z"


def test_formatter_is_utc_not_local_time():
    fmt = UtcIsoFormatter("%(asctime)s")
    # gmtime, not localtime: same epoch must render identically regardless of TZ.
    assert fmt.format(_record(0.0, 0)) == "1970-01-01T00:00:00.000Z"
