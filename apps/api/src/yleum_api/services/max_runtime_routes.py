"""Helpers for picking a meaningful MAX runtime route during signed preview proof."""

from __future__ import annotations

import re
from collections.abc import Mapping

_MAX_HOME_PAGE = "src/app/page.tsx"
MAX_PRODUCT_READINESS_PATH = "/api/yleum-readiness"
_READ_HANDLER = re.compile(r"(?m)^\s*export\s+(?:(?:async\s+)?function|const)\s+GET\b")
_PORTABLE_ROOT_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("/support", "src/app/support/page.tsx"),
    ("/legal/privacy", "src/app/legal/privacy/page.tsx"),
    ("/legal/terms", "src/app/legal/terms/page.tsx"),
)


def resolve_max_business_probe_path(files: Mapping[str, str]) -> str | None:
    """Opt in via the reserved, read-only product endpoint, without guessing API paths."""
    for extension in ("ts", "js"):
        source = files.get(f"src/app{MAX_PRODUCT_READINESS_PATH}/route.{extension}", "")
        if _READ_HANDLER.search(source):
            return MAX_PRODUCT_READINESS_PATH
    return None


def _nonempty_file(files: Mapping[str, str], path: str) -> bool:
    return bool(str(files.get(path) or "").strip())


def resolve_max_runtime_probe_paths(
    files: Mapping[str, str],
    *,
    requested_path: str = "/",
) -> tuple[str, tuple[str, ...]]:
    """Keep ``/`` strict once the home page exists, but offer stable fallbacks before it does.

    MAX starter v13 intentionally has no product home page. During that window we
    still need a deterministic signed-preview proof without pretending that the
    final home page already renders. The legal/support routes are platform-owned
    and always present in the seeded core, so they are safe fallbacks for an
    intermediate runtime/data-plane check.
    """

    normalized_path = str(requested_path or "/")
    if normalized_path != "/" or _nonempty_file(files, _MAX_HOME_PAGE):
        return normalized_path, ()
    fallback_paths = tuple(
        route
        for route, source_path in _PORTABLE_ROOT_FALLBACKS
        if _nonempty_file(files, source_path)
    )
    return normalized_path, fallback_paths


__all__ = [
    "MAX_PRODUCT_READINESS_PATH",
    "resolve_max_business_probe_path",
    "resolve_max_runtime_probe_paths",
]
