"""Парсер AI-ответа в формате <file path="...">...</file> + санитизация путей."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_FILE_BLOCK = re.compile(
    r'<file\s+path="(?P<path>[^"]+)"\s*>(?P<body>.*?)</file>',
    re.DOTALL,
)

_FORBIDDEN_PREFIXES = ("/", "~", ".git/", ".git\\")
_FORBIDDEN_SUBSTRINGS = ("..",)
MAX_FILES = 100
MAX_FILE_BYTES = 2 * 1024 * 1024


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


def extract_files(answer: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for match in _FILE_BLOCK.finditer(answer):
        raw_path = match.group("path").strip()
        body = match.group("body")
        if not is_safe_path(raw_path):
            raise UnsafePathError(f"unsafe file path: {raw_path!r}")
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"file {raw_path} exceeds {MAX_FILE_BYTES} bytes")
        files[raw_path] = body
        if len(files) > MAX_FILES:
            raise ValueError(f"too many files in answer: {len(files)} > {MAX_FILES}")
    return files
