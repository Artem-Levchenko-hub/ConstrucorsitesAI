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

from uuid import UUID

from omnia_api.core.config import get_settings
from omnia_api.services import accept_gauntlet
from omnia_api.services.accept_gauntlet import GauntletVerdict
from omnia_api.services.dev_container import resolve_live_url


async def gate_live_app(project_id: UUID, slug: str) -> GauntletVerdict | None:
    """Run the desktop-width composition legs (taste + hierarchy) against the
    live entity container and return the verdict.

    Returns ``None`` (the caller skips the card this round) when the gate is
    disabled, the container isn't reachable, or the gauntlet errors. Only the
    ``COMPOSITION_LEGS`` run (``include_rendered=False`` keeps the 44px touch
    leg out — it false-positives on good shadcn buttons and is calibrated
    separately, 11/5). No ``files`` are passed, so the source-scan registry
    leg is skipped — a container app has no static ``files`` dict here.
    """
    if not get_settings().acceptance_entity_composition_gate:
        return None
    url = await resolve_live_url(project_id)
    if url is None:
        return None
    try:
        return await accept_gauntlet.run(
            url=url, include_rendered=False, composition=True
        )
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
