"""RQ job: composition-gauntlet an entity/fullstack app's LIVE container.

V1.6 16/5. Freeform / catalog pages run the composition floor inline in the API
process (``acceptance.evaluate`` renders a static ``files`` dict). Entity /
fullstack apps can't: they live in a Docker container reachable only on the
``omnia-runtime`` network, and only the WORKER joins that network (the API
process is on ``omnia-prod`` only). So the live-container composition gate runs
HERE, in the worker, where Playwright can reach ``omnia-dev-<slug>:3000``
container-to-container — the same path the preview-thumbnail job already uses.

Enqueued right after a clean hot-reload (``enqueue_entity_gate``). It waits for
Turbopack to settle (a compile-broken app is surfaced separately by the API's
compile probe — we skip it here), fans the COMPOSITION_LEGS over the live URL,
and surfaces a ``hard_failed`` as a quality card on the assistant message.

Fully fail-soft (R-10): any hiccup — unreachable container, compile still
broken, render error — skips the card. A missing quality card is acceptable; a
broken build is not.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from omnia_api.core.config import get_settings
from omnia_api.services import (
    app_errors,
    dev_container,
    entity_gate,
    orchestrator_client,
    route_target,
)

#: Bounded wait for Turbopack to finish recompiling after the hot-reload before
#: we render — mirrors the API compile probe's ~9s budget (3 × 3s).
_COMPILE_SETTLE_TRIES = 3
_COMPILE_SETTLE_DELAY = 3.0


async def _compile_clean(project_id: UUID, slug: str) -> bool:
    """Poll the dev container until Turbopack reports a clean compile (or we run
    out of tries). ``ok=True`` means clean OR still compiling — either way the
    render-settle inside the gauntlet handles the rest. A hard compile error
    (``ok=False``) means there's nothing worth scoring; the API probe surfaces
    that error separately, so we just skip."""
    for _ in range(_COMPILE_SETTLE_TRIES):
        await asyncio.sleep(_COMPILE_SETTLE_DELAY)
        try:
            status = await orchestrator_client.compile_status(project_id, slug=slug)
        except Exception:
            return False  # orchestrator hiccup — skip, best-effort
        if not status.get("ok", True):
            return False  # compile broken — nothing to score, API probe owns it
    return True


async def _gate_async(message_id: str, project_id: str, slug: str) -> None:
    settings = get_settings()
    if not settings.acceptance_entity_composition_gate:
        return
    pid = UUID(project_id)
    mid = UUID(message_id)

    if not await _compile_clean(pid, slug):
        return

    # 16/5d — target the WOW/content surface, not the bare `/` (a login wall for
    # an auth-gated app). Resolve the bare URL once, probe it for the right route,
    # then gate that route. Fail-soft: probe resolves to `/` on any hiccup.
    base = await dev_container.resolve_live_url(pid)
    route = "/" if base is None else await route_target.resolve_target_route(base)

    verdict = await entity_gate.gate_live_app(pid, slug, route)
    if verdict is None or not verdict.hard_failed:
        return
    print(
        f"[quality] entity composition hard-fail {slug}: "
        f"{list(verdict.failed_classes)}",
        flush=True,
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await app_errors.publish(
            factory,
            pid,
            mid,
            category="runtime",
            title="Дизайн ниже планки качества",
            detail=entity_gate.describe_failure(verdict),
        )
    finally:
        await engine.dispose()


def gate_entity_app(message_id: str, project_id: str, slug: str) -> None:
    """Sync RQ entrypoint (the queue passes plain strings)."""
    asyncio.run(_gate_async(message_id, project_id, slug))
