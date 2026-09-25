"""Helpers for picking a meaningful MAX runtime route during signed preview proof."""

from __future__ import annotations

from collections.abc import Mapping

_MAX_HOME_PAGE = "src/app/page.tsx"
_PORTABLE_ROOT_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("/support", "src/app/support/page.tsx"),
    ("/legal/privacy", "src/app/legal/privacy/page.tsx"),
    ("/legal/terms", "src/app/legal/terms/page.tsx"),
)


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


__all__ = ["resolve_max_runtime_probe_paths"]
