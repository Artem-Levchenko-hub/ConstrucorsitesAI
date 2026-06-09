"""Detect a current Next.js / Turbopack compile error from dev-server logs.

The dev container runs `next dev --turbopack`. When the AI writes broken TSX,
Turbopack prints an error block (a U+2A2F error glyph + ``./src/...`` +
``Module not found`` / ``Ecmascript file had an error`` / a code-frame) to
stdout instead of the usual success line. apps/api polls this right after a
hot-reload so the
chat can surface the failure as a card instead of leaving the user staring at a
broken preview.

Pure string logic — no Docker/IO — so it is unit-testable against captured log
samples (see tests/test_compile_status.py).
"""

from __future__ import annotations

import re

# Strip ANSI colour codes Next.js emits so substring/regex matching is stable.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Turbopack prefixes each error line with U+2A2F. Built via chr() so the literal
# glyph doesn't trip ambiguous-unicode linting (it's load-bearing — we match it).
_TURBOPACK_ERR_GLYPH = chr(0x2A2F)

# Strong, low-false-positive signals that the dev server currently has a broken
# compile (or an unhandled error on the route it just tried to render).
_FAIL_MARKERS: tuple[str, ...] = (
    "Failed to compile",
    "Module not found",
    "Ecmascript file had an error",
    "Parsing ecmascript source code failed",
    "Build error occurred",
    "Type error:",
    "Unhandled Runtime Error",
    _TURBOPACK_ERR_GLYPH,
)

# A later success supersedes an earlier failure (HMR recovery): only a failure
# NEWER than the last success is reported, so a fixed app stops crying wolf.
_OK_MARKERS: tuple[str, ...] = (
    "✓ Compiled",  # ✓ Compiled /…
    "Compiled successfully",
    "✓ Ready",  # ✓ Ready in …
    "compiled client and server successfully",
)

# First source file implicated by the error block. Scoped to project source
# dirs so we don't latch onto node_modules / framework internals.
# Note: parens are allowed — Next.js route groups are real path segments
# (``src/app/(app)/page.tsx``). The match ends at the file extension, so the
# trailing ``:line:col`` Turbopack appends is left out.
_FILE_RE = re.compile(
    r"\.?/?(?:src|app|components|lib)/[^\s:'\"]+\.(?:tsx|ts|jsx|js|mjs|css)"
)

_BLOCK_LINES = 14  # how many lines after the failure marker form the detail
_DETAIL_CAP = 600  # keep the card payload bounded


def parse_next_compile_error(logs: str) -> tuple[bool, str | None, str | None]:
    """Return ``(ok, error_text, file)`` for the *current* dev-server state.

    ``ok=True`` — no outstanding compile error (compiling cleanly, or the last
    failure was already superseded by a later successful compile).
    ``ok=False`` — ``error_text`` is a compact, ANSI-stripped excerpt and
    ``file`` is the first implicated project source file (or ``None``).

    Conservative by design: false negatives (miss an error) are preferable to
    false positives (a spurious red card on a healthy app).
    """
    if not logs:
        return True, None, None

    lines = _ANSI_RE.sub("", logs).splitlines()

    last_ok = _last_index(lines, _OK_MARKERS)
    # The current error block starts at the FIRST failure marker after the last
    # successful compile (a later success supersedes earlier errors). Anchor to
    # the block start, not the last marker — the implicated file path is usually
    # on the very first line (the error-glyph line) while later lines carry
    # follow-on markers (``Module not found``).
    start = -1 if last_ok is None else last_ok
    block_start = _first_index_after(lines, _FAIL_MARKERS, after=start)
    if block_start is None:
        return True, None, None

    window = lines[block_start : block_start + _BLOCK_LINES]
    detail = "\n".join(line.rstrip() for line in window if line.strip())[
        :_DETAIL_CAP
    ].strip()
    file = _first_project_file("\n".join(window))

    return False, detail or None, file


def _last_index(lines: list[str], markers: tuple[str, ...]) -> int | None:
    """Index of the last line containing any marker, or None."""
    found: int | None = None
    for i, line in enumerate(lines):
        if any(m in line for m in markers):
            found = i
    return found


def _first_index_after(
    lines: list[str], markers: tuple[str, ...], *, after: int
) -> int | None:
    """Index of the first line after ``after`` containing any marker, or None."""
    for i in range(after + 1, len(lines)):
        if any(m in lines[i] for m in markers):
            return i
    return None


def _first_project_file(text: str) -> str | None:
    """First implicated project source file, skipping framework/dep paths."""
    for match in _FILE_RE.finditer(text):
        candidate = match.group(0).lstrip("./")
        if "node_modules" in candidate or "/.next/" in candidate or "/dist/" in candidate:
            continue
        return candidate or None
    return None
