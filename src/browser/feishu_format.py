from __future__ import annotations

import re
import unicodedata


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
    text = _tables_to_code_blocks(markdown or "")
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        lines.append(_safe_list_line(line))
    return "\n".join(lines).strip()


def _disp_width(text: str) -> int:
    """Display width counting CJK/full-width glyphs as 2 columns (so monospace
    alignment lines up on Feishu)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _is_pipe_row(line: str) -> bool:
    s = line.strip()
    return "|" in s and (s.startswith("|") or s.count("|") >= 2)


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    return bool(s) and "-" in s and "|" in s and re.fullmatch(r"[\s|:\-]+", s) is not None


def _parse_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _render_table_block(block: list[str]) -> str:
    """Markdown pipe table -> CJK-width-aligned monospace, wrapped in a code fence
    (lark_md can't render pipe tables but renders ``` blocks with columns lined up)."""
    rows = [_parse_cells(block[0])] + [_parse_cells(r) for r in block[2:]]
    cols = max(len(r) for r in rows)
    widths = [0] * cols
    for r in rows:
        for c in range(cols):
            cell = r[c] if c < len(r) else ""
            widths[c] = max(widths[c], _disp_width(cell))
    out_lines = []
    for r in rows:
        parts = []
        for c in range(cols):
            cell = r[c] if c < len(r) else ""
            parts.append(cell + " " * (widths[c] - _disp_width(cell)))
        out_lines.append("  ".join(parts).rstrip())
    return "```\n" + "\n".join(out_lines) + "\n```"


def _tables_to_code_blocks(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if (
            not in_fence
            and _is_pipe_row(line)
            and i + 1 < n
            and _is_table_separator(lines[i + 1])
        ):
            block = [line, lines[i + 1]]
            j = i + 2
            while j < n and _is_pipe_row(lines[j]) and not _is_table_separator(lines[j]):
                block.append(lines[j])
                j += 1
            out.append(_render_table_block(block))
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


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
