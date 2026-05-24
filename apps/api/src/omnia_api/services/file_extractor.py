"""Парсер AI-ответа в формате <file path="...">...</file> + санитизация путей."""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

log = logging.getLogger(__name__)

_FILE_BLOCK = re.compile(
    r'<file\s+path="(?P<path>[^"]+)"\s*>(?P<body>.*?)</file>',
    re.DOTALL,
)

_FORBIDDEN_PREFIXES = ("/", "~", ".git/", ".git\\")
_FORBIDDEN_SUBSTRINGS = ("..",)
MAX_FILES = 100
MAX_FILE_BYTES = 2 * 1024 * 1024

# Models occasionally violate the "unchanged files: don't mention" contract
# and return a <file> block whose body is a human-language placeholder like
# "(код без изменений)". Writing that into the file produces a TypeScript /
# Python / HTML parse error and breaks the dev container. We detect such
# stubs and silently skip them — the prior file content stays intact.
_PLACEHOLDER_SIGNATURES = (
    "код без изменений",
    "без изменений",
    "не изменён",
    "не изменен",
    "unchanged",
    "no changes",
    "no change",
    "same as before",
    "as is",
    "оставить как есть",
)


class UnsafePathError(ValueError):
    pass


def is_safe_path(path: str) -> bool:
    if not path or path.startswith(_FORBIDDEN_PREFIXES):
        return False
    if any(s in path for s in _FORBIDDEN_SUBSTRINGS):
        return False
    if "\x00" in path:
        return False
    try:
        normalized = PurePosixPath(path)
    except (ValueError, TypeError):
        return False
    if normalized.is_absolute() or normalized.anchor:
        return False
    return True


_CODE_SYNTAX_CHARS = set("{};=()<>[]")


def _looks_like_unchanged_stub(body: str) -> bool:
    """True when the body is a placeholder ("no changes") instead of real content.

    Heuristic:
    * short body (<= 80 chars after strip),
    * contains a known placeholder signature,
    * AND has essentially no code syntax — `{`, `}`, `(` (other than wrapping the
      phrase), `;`, `=`, `<`, `>`, `[`, `]`. Real code of any length normally
      has at least a couple of these; "(код без изменений)" has only `(` and `)`.

    Keeping the threshold tight (<=80) prevents false-positives on real code
    that happens to mention "unchanged" in a comment or identifier.
    """
    stripped = body.strip()
    if not stripped or len(stripped) > 80:
        return False
    low = stripped.lower()
    if not any(sig in low for sig in _PLACEHOLDER_SIGNATURES):
        return False
    # Strip the most common wrappers: ()/<!--/-->/// and whitespace, then count
    # remaining code-syntax chars. Wrapper parens around a Russian phrase are
    # not code; semicolons/braces/equals/angle brackets in a 80-char body are.
    significant_syntax = sum(1 for ch in stripped if ch in {"{", "}", ";", "=", "[", "]"})
    if significant_syntax > 0:
        return False
    return True


def extract_files(answer: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for match in _FILE_BLOCK.finditer(answer):
        raw_path = match.group("path").strip()
        body = match.group("body")
        if not is_safe_path(raw_path):
            raise UnsafePathError(f"unsafe file path: {raw_path!r}")
        if _looks_like_unchanged_stub(body):
            log.warning(
                "extract_files: skipping placeholder stub for %r (body=%r)",
                raw_path,
                body.strip()[:80],
            )
            continue
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"file {raw_path} exceeds {MAX_FILE_BYTES} bytes")
        files[raw_path] = body
        if len(files) > MAX_FILES:
            raise ValueError(f"too many files in answer: {len(files)} > {MAX_FILES}")
    return files
