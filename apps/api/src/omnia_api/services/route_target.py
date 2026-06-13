"""Pick the WOW/content route the composition gate should score (V1.6 16/5d).

The entity composition gate (16/5) navigates the bare container URL ``/`` and
scores it with the landing-richness rubric (taste 7/5 + hierarchy 9/5). For a
marketing-landing app (fitness / shop / school) ``/`` *is* the WOW surface and
that is exactly right. For an auth-gated app (CRM / fintech) ``/`` is a centred
login card — the wrong SURFACE for a landing rubric, so it false-fails (16/5d's
first cut waived it; see :mod:`surface_class`).

This module targets the content route directly instead of waiving. It renders
``/`` once and classifies it (R-04: it reuses ``taste_gate``'s one ``_AUDIT_JS``
extraction and ``surface_class`` — the gate and the probe therefore see the
*same* DOM, so a route the probe calls "content" is a route the gate can score):

  * ``/`` is NOT a login surface  → score ``/`` (the common, marketing-landing
    case; route-targeting is a no-op).
  * ``/`` IS a login surface      → probe the candidate content route
    (``/dashboard`` by default). Only target it if the candidate itself rendered
    a *content* surface. A client app whose ``/dashboard`` redirects back to the
    login (no public, unauthenticated dashboard) renders another login/sparse
    surface there too → we fall back to ``/`` and the gate's login waiver owns
    it. So we never gate a redirect-to-login one hop later — the teeth stay on
    real broken pages, never on an intentional auth wall.

Fail-soft throughout (R-10): any probe/render hiccup → ``/`` (gate the root as
before), never a raise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from omnia_api.services import taste_gate
from omnia_api.services.render_settle import goto_and_settle
from omnia_api.services.surface_class import above_fold_text_count, is_login_surface

if TYPE_CHECKING:
    from playwright.async_api import Page

Obs = dict[str, Any]

#: Default content route to try when ``/`` is a login wall. Generated entity apps
#: put the authenticated app shell under ``(app)/dashboard`` — the one route name
#: every nextjs-entities app shares.
DEFAULT_CANDIDATE_ROUTE = "/dashboard"

#: A content surface carries real above-the-fold substance. A login card is ~8
#: nodes (``_LOGIN_MAX_AFOLD_TEXTS`` = 24 in :mod:`surface_class`); a redirect or
#: an empty client shell is near-zero. This floor sits below a thin dashboard but
#: above a skeleton, so we only divert to a route that actually rendered content.
_MIN_CONTENT_AFOLD = 12

#: Desktop composition width — the probe must see what the gate sees (R-04).
_PROBE_WIDTH = taste_gate.GATE_WIDTH
_PROBE_HEIGHT = taste_gate.GATE_HEIGHT


def _is_content_surface(obs: Obs) -> bool:
    """True when the observation is a real content page (not login, not empty).

    Pure mirror of the gate's own reads: not a login wall (``surface_class``) and
    a non-trivial above-the-fold (so a redirect-to-login or a hollow shell does
    not qualify)."""
    if is_login_surface(obs):
        return False
    return above_fold_text_count(obs) >= _MIN_CONTENT_AFOLD


def pick_route(
    root_obs: Obs,
    candidate_obs: Obs | None,
    candidate_route: str = DEFAULT_CANDIDATE_ROUTE,
) -> str:
    """Decide which route the composition gate should score. Pure / browser-free.

    ``root_obs`` is the rendered ``/``; ``candidate_obs`` is the rendered
    candidate content route (or ``None`` when it wasn't probed because ``/`` was
    already content). Returns ``/`` unless ``/`` is a login wall AND the candidate
    rendered a genuine content surface — in which case it returns the candidate
    route. Never diverts onto another login/empty surface (teeth stay intact)."""
    if not is_login_surface(root_obs):
        return "/"
    if candidate_obs is not None and _is_content_surface(candidate_obs):
        return candidate_route
    return "/"


async def _probe(url: str, *, timeout_ms: int = 15_000) -> Obs:
    """Render ``url`` at desktop width and return the raw taste observation.

    Reuses ``taste_gate._AUDIT_JS`` (R-04: the gate's single extraction) so the
    probe and the gate read the same DOM. Raises on a real navigation failure —
    the caller fail-softs to ``/``."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page: Page = await browser.new_page(
                viewport={"width": _PROBE_WIDTH, "height": _PROBE_HEIGHT},
                reduced_motion="reduce",
            )
            try:
                await goto_and_settle(page, url, timeout_ms=timeout_ms)
                obs: Obs = await page.evaluate(taste_gate._AUDIT_JS)
                return obs
            finally:
                await page.close()
        finally:
            await browser.close()


async def resolve_target_route(
    base_url: str, *, candidate_route: str = DEFAULT_CANDIDATE_ROUTE
) -> str:
    """Render-probe ``base_url`` and return the route the composition gate should
    score (``/`` or ``candidate_route``). Fail-soft (R-10): any hiccup → ``/``.

    ``base_url`` is the bare container URL (``http://<name>:3000``) — i.e. ``/``.
    Probes the candidate route ONLY when ``/`` is a login wall (the minority
    case), so a marketing-landing app pays for exactly one probe render."""
    try:
        root = await _probe(base_url)
    except Exception as exc:  # render hiccup — gate the root as before
        print(f"[route_target] root probe failed for {base_url}: {exc!r}", flush=True)
        return "/"
    if not is_login_surface(root):
        return "/"
    candidate_url = base_url.rstrip("/") + candidate_route
    try:
        candidate = await _probe(candidate_url)
    except Exception as exc:
        print(
            f"[route_target] candidate probe failed for {candidate_url}: {exc!r}",
            flush=True,
        )
        return "/"
    return pick_route(root, candidate, candidate_route)


__all__ = [
    "DEFAULT_CANDIDATE_ROUTE",
    "pick_route",
    "resolve_target_route",
]
