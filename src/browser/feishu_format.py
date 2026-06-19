from __future__ import annotations

import re


_TASK_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+\[(?P<checked>[ xX])\]\s+(?P<body>.*)$")
_UNORDERED_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<body>.*)$")
_ORDERED_RE = re.compile(r"^(?P<indent>\s*)(?P<num>\d+)\.\s+(?P<body>.*)$")


def feishu_safe_markdown(markdown: str) -> str:
    """Avoid Feishu post-md swallowing plain list blocks.

    OpenClaw's Feishu auto mode sends list-only replies as post md, not cards.
    Literal bullets / escaped numeric markers keep the text visible there while
    preserving code fences unchanged.
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
        return f"{ordered.group('indent')}{ordered.group('num')}\\. {ordered.group('body')}"

    return line
