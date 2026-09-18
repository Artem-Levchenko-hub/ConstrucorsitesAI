"""AV06: an adaptation must not silently drop functions the draft already had.

The draft before an adaptation run serves real data (e.g. ``GET /api/visits``).
Restoring older screens is allowed; losing the way to read or change existing
data is not, unless the owner explicitly retires that function. This is a
deterministic source check (HTTP route methods of the Next.js app router), not
a prompt. The same extraction lives in the orchestrator
(``services/versioning/compatibility.py``); the services share no package.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_ROUTE_FILE = re.compile(r"^src/app/(?P<dir>(?:.+/)?)route\.(?:ts|js|tsx|jsx|mjs)$")
_EXPORT = re.compile(
    r"export\s+(?:async\s+)?(?:function\s+|const\s+)(GET|POST|PUT|PATCH|DELETE)\b"
)
# The managed MAX kit serves these routes in every version.
_PLATFORM_PREFIXES = ("/api/omnia/", "/api/max/")
_PLATFORM_ROUTES = {"/api/health"}


def route_capabilities(files: Mapping[str, str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path, text in files.items():
        match = _ROUTE_FILE.match(path)
        if not match or not isinstance(text, str):
            continue
        segments = [
            segment
            for segment in match["dir"].strip("/").split("/")
            if segment and not (segment.startswith("(") and segment.endswith(")"))
        ]
        route = "/" + "/".join(segments)
        if route.startswith(_PLATFORM_PREFIXES) or route in _PLATFORM_ROUTES:
            continue
        for method in _EXPORT.findall(text):
            found.add((method, route))
    return found


def lost_capabilities(
    before: Mapping[str, str], after: Mapping[str, str]
) -> list[tuple[str, str]]:
    return sorted(route_capabilities(before) - route_capabilities(after))


def capability_gap(before: Mapping[str, str], after: Mapping[str, str]) -> str | None:
    """Repair feedback for the agent, or None when every function is kept."""
    lost = lost_capabilities(before, after)
    if not lost:
        return None
    routes = ", ".join(f"{method} {path}" for method, path in lost)
    return (
        "Адаптация удалила функции, которые были в черновике до неё: "
        f"{routes}. Данные для них остаются в базе. Верни эти маршруты рабочими "
        "(тот же формат ответа и та же проверка владельца записей), даже если "
        "экраны выбранной версии их не используют. "
        "ADAPTATION CAPABILITY CHECK: keep every listed route exported and working."
    )
