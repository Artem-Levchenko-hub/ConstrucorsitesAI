"""Route pre-warm — compile the main pages BEFORE a human opens the preview.

The preview runs `next dev --turbopack`, and Turbopack compiles each route lazily
on its FIRST request (a cold compile of a real generated page is ~30-90 s). Nothing
requests a route until a person clicks it — so on a demo the reviewer eats that
cold compile live, per page. This service fires those first requests itself, right
after a successful build, so the routes are already warm when the reviewer lands.

Pure best-effort: it never raises into the caller and never blocks the build — a
warm failure just means the old (slow-first-hit) behaviour, never a broken app.

Route discovery: `find src/app` for `page.tsx`, map each to its URL by dropping
`src/app`, the trailing `/page.tsx`, and every `(group)` segment (route groups
don't affect the URL). Dynamic (`[id]`), parallel/intercept (`@`, `(.)`), private
(`_`), and `api/` segments are skipped — they need params or aren't pages.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from omnia_orchestrator.core.docker_client import container_status, exec_cmd

log = structlog.get_logger(__name__)

# A cold Turbopack compile of one route can take up to ~90 s; cap per-route so a
# single slow page can't hang the warm sweep. Routes are hit in parallel, so the
# whole sweep is ~the slowest single route, not the sum.
_WARM_TIMEOUT = 75.0
# Bound the sweep — a huge app doesn't need every route warm for a demo; the main
# nav pages are what a reviewer clicks. Keeps concurrent compiles from thrashing.
_MAX_ROUTES = 12

_ROUTE_GROUP_RE = re.compile(r"/\([^/]+\)")  # `/(app)` → "" (does not affect URL)


def _page_path_to_route(rel: str) -> str | None:
    """Map a `src/app/.../page.tsx` path to its URL route, or None to skip.

    Skips dynamic/parallel/intercept/private/api segments — they can't be warmed
    with a plain GET (need params) or aren't real pages.
    """
    p = rel.strip().replace("\\", "/")
    p = p.removeprefix("./").removeprefix("src/app").removeprefix("/app")
    p = re.sub(r"/page\.(tsx|jsx|ts|js)$", "", p)
    # Skip non-static segments anywhere in the path.
    if re.search(r"/(\[|@|_|api(/|$))", p) or "(." in p:
        return None
    route = _ROUTE_GROUP_RE.sub("", p)  # drop route groups
    route = re.sub(r"//+", "/", route)  # collapse doubles left by group removal
    return route or "/"


async def _discover_routes(name: str) -> list[str]:
    """Static page routes of the app, `/` first. Best-effort — empty on any error."""
    try:
        res = await exec_cmd(
            name,
            ["sh", "-c", "find src/app -type f -name 'page.tsx' 2>/dev/null || true"],
            timeout_sec=15,
            max_output=16_000,
        )
    except Exception as exc:  # noqa: BLE001 — warm is best-effort
        log.info("warm.discover_failed", name=name, err=str(exc))
        return []
    routes: list[str] = []
    for line in (res.get("stdout") or "").splitlines():
        r = _page_path_to_route(line)
        if r and r not in routes:
            routes.append(r)
    # `/` first (the landing a reviewer sees), then the rest; bound the total.
    routes.sort(key=lambda r: (r != "/", r))
    return routes[:_MAX_ROUTES]


async def warm_routes(name: str) -> dict[str, int | str]:
    """GET every static route once (in parallel) to force its Turbopack compile.

    Returns a small summary. Never raises — a warm failure degrades to the normal
    cold-first-hit behaviour.
    """
    try:
        status = await container_status(name)
    except Exception as exc:  # noqa: BLE001
        return {"warmed": 0, "note": f"status error: {type(exc).__name__}"}
    if status.get("state") != "running":
        return {"warmed": 0, "note": "not running"}
    port = status.get("port")
    if not port:
        return {"warmed": 0, "note": "no port"}

    routes = await _discover_routes(name)
    if not routes:
        routes = ["/"]

    async def _hit(client: httpx.AsyncClient, path: str) -> bool:
        try:
            r = await client.get(f"http://127.0.0.1:{port}{path}")
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    async with httpx.AsyncClient(timeout=_WARM_TIMEOUT) as client:
        results = await asyncio.gather(*(_hit(client, p) for p in routes))

    warmed = sum(1 for ok in results if ok)
    log.info("warm.done", name=name, warmed=warmed, total=len(routes))
    return {"warmed": warmed, "total": len(routes)}
