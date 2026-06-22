"""V1.6 16/5 — composition gauntlet on the LIVE entity / fullstack app.

Freeform / catalog pages ship as a static ``files`` dict and run through
``acceptance.evaluate`` (the freeform safety net + the always-on composition
legs, 14/5). Entity / fullstack apps are different: they build into a Docker
container served at ``http://omnia-dev-<slug>:3000`` on the shared runtime
network, and ``acceptance.evaluate`` *explicitly skips them*
(``messages.py`` filters ``_gen_mode in (freeform, catalog)``). So the
awwwards composition floor — taste + hierarchy, the heart of pillar 1 — was
asserted on ZERO entity apps, even though the whole pillar-1 corpus (clinic /
shop / CRM / school / fintech / fitness) is ``nextjs_entities``. The beauty
floor was gated on a branch the flagship barely uses.

This module closes that gap. After a clean hot-reload + compile-settle, the
caller fans the ``COMPOSITION_LEGS`` over the LIVE internal URL
(container-to-container, NO public egress) and surfaces a ``hard_failed`` as a
quality card — the live-container analog of a ship-block (the app is already
running, so the deterministic action is "regenerate", not "re-roll").

Canon: R-01 (one deep call hides URL-resolve + gauntlet + flag), R-04 (reuses
``dev_container.resolve_live_url`` and ``accept_gauntlet`` — no duplicated URL
math or gate logic), R-10 (fail-soft: unreachable / disabled / render error →
``None`` or an abstain, never a false hard-fail).
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from omnia_api.core.config import get_settings
from omnia_api.services import accept_gauntlet
from omnia_api.services.accept_gauntlet import GauntletVerdict
from omnia_api.services.dev_container import resolve_live_url


def _merge_verdicts(desktop: GauntletVerdict, mobile: GauntletVerdict) -> GauntletVerdict:
    """Union a desktop verdict with a @390 composition pass. The mobile composition
    legs are renamed ``taste@390`` / ``hierarchy@390`` so they add findings without
    colliding with (or overwriting) their desktop namesakes. Only the composition
    legs ride along from the mobile pass — everything else stays desktop."""
    extra = tuple(
        replace(g, gate=f"{g.gate}@390")
        for g in mobile.gates
        if g.gate in (accept_gauntlet.TASTE, accept_gauntlet.HIERARCHY)
    )
    return GauntletVerdict(
        desktop.gates + extra, render_expected=desktop.render_expected
    )


async def gate_live_app(
    project_id: UUID,
    slug: str,
    route: str = "/",
    *,
    storage_state: dict | None = None,
) -> GauntletVerdict | None:
    """Run the composition legs (taste + hierarchy) against the live entity
    container and return the verdict.

    ``route`` (V1.6 16/5d) selects the surface to score — the caller resolves the
    WOW/content route via :mod:`route_target` and passes it here, so the gate
    scores the dashboard / landing rather than the bare ``/`` login wall. The
    default ``/`` keeps the historical behaviour.

    ``storage_state`` (Area C, DARK) is an authenticated Playwright session from
    :func:`auth_session.establish_session`. When present (the gate logged into the
    app), the gauntlet renders the REAL cabinet: it fans the composition floor at
    desktop AND an adaptive @390 pass (rich-on-desktop / monotone-on-mobile), plus
    the ADVISORY cabinet-states leg (empty-state / onboarding / stuck-skeleton).
    When ``None`` (flag off / login failed) behaviour is byte-identical to before —
    desktop composition on the resolved route, no cabinet leg, no @390.

    Returns ``None`` (the caller skips the card this round) when the gate is
    disabled, the container isn't reachable, or the gauntlet errors. No ``files``
    are passed, so the source-scan registry leg is skipped — a container app has no
    static ``files`` dict here.
    """
    if not get_settings().acceptance_entity_composition_gate:
        return None
    url = await resolve_live_url(project_id, route)
    if url is None:
        return None
    authed = storage_state is not None
    try:
        verdict = await accept_gauntlet.run(
            url=url,
            include_rendered=False,
            composition=True,
            cabinet=authed,  # cabinet-states leg only when we can see the cabinet
            storage_state=storage_state,
        )
        if authed:
            # Adaptive @390 composition pass — catch a cabinet that's rich on
            # desktop but a monotone column on mobile. Merged in (renamed legs).
            mobile = await accept_gauntlet.run(
                url=url,
                include_rendered=False,
                composition=True,
                composition_width=390,
                storage_state=storage_state,
            )
            verdict = _merge_verdicts(verdict, mobile)
        return verdict
    except Exception as exc:  # render / browser hiccup — never sink the build
        print(f"[entity_gate] composition gauntlet error for {slug}: {exc!r}", flush=True)
        return None


def describe_failure(verdict: GauntletVerdict) -> str:
    """User-facing Russian detail for a composition hard-fail card."""
    classes = ", ".join(verdict.failed_classes) or "композиция"
    return (
        "Сгенерированное приложение не проходит композиционный пол "
        f"(taste / hierarchy): {classes}. Перегенерируй с акцентом на "
        "визуальную иерархию, контраст и фактуру героя."
    )
