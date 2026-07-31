from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from omnia_api.services.hero_media_pipeline import fail_hero_media_render, run_hero_media_render

log = structlog.get_logger(__name__)


def hero_media_job(render_id: str) -> None:
    asyncio.run(_run(UUID(render_id)))


async def _run(render_id: UUID) -> None:
    try:
        await run_hero_media_render(render_id)
    except Exception as exc:
        log.exception("hero_media.render_failed", render_id=str(render_id), err=str(exc))
        await fail_hero_media_render(render_id, str(exc))
