from __future__ import annotations

import re


_TASK_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+\[(?P<checked>[ xX])\]\s+(?P<body>.*)$")
_UNORDERED_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<body>.*)$")
_ORDERED_RE = re.compile(r"^(?P<indent>\s*)(?P<num>\d+)\.\s+(?P<body>.*)$")


def feishu_safe_markdown(markdown: str) -> str:
    """Avoid Feishu post-md swallowing plain list blocks.

    OpenClaw's Feishu auto mode sends list-only replies as post md, not cards.
    Literal bullets / a fullwidth numeric period keep the text visible there
    while preserving code fences unchanged.

    Why fullwidth period (．) instead of an escaped ``\\.`` for ordered items:
    the markdown escape is parsed and rendered cleanly on Feishu web/desktop,
    but the Android client renders the backslash literally (you see ``1\\.``).
    A fullwidth period is just text — not list syntax — so post md cannot fold
    it, every Feishu client renders the same character, and it visually mirrors
    the original ASCII period.
    """
    lines: list[str] = []
    in_fence = False
    for line in (markdown or "").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        lines.append(_safe_list_line(line))
    return "\n".join(lines).strip()


def _safe_list_line(line: str) -> str:
    task = _TASK_RE.match(line)
    if task:
        box = "☑" if task.group("checked").lower() == "x" else "☐"
        return f"{task.group('indent')}{box} {task.group('body')}"

    unordered = _UNORDERED_RE.match(line)
    if unordered:
        return f"{unordered.group('indent')}• {unordered.group('body')}"

    ordered = _ORDERED_RE.match(line)
    if ordered:
        return f"{ordered.group('indent')}{ordered.group('num')}． {ordered.group('body')}"

    return line
