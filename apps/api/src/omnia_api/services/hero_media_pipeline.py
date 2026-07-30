from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_engine
from omnia_api.core.redis import publish_event
from omnia_api.models.hero_media_asset import HeroMediaAsset
from omnia_api.models.hero_media_brief import HeroMediaBrief
from omnia_api.models.hero_media_render import HeroMediaRender
from omnia_api.schemas.hero_media import HeroMediaDecision, HeroMediaPlanKind
from omnia_api.services import agent_media
from omnia_api.services.hero_media_assembler import build_hero_bundle
from omnia_api.services.image_resolver import generate_and_store_image


def _now() -> datetime:
    return datetime.now(UTC)


def _storage_key_from_url(url: str, bucket: str) -> str | None:
    path = urlparse(url).path.strip("/")
    prefix = f"{bucket}/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return None


def _guess_mime(url: str, fallback: str) -> str:
    lower = url.lower()
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    return fallback


def _append_progress(render: HeroMediaRender, *, status: str, detail: str) -> None:
    log = list(render.progress_log or [])
    log.append({"at": _now().isoformat(), "status": status, "detail": detail})
    render.progress_log = log
    render.status = status
    render.status_detail = detail


async def _publish_status(render: HeroMediaRender) -> None:
    await publish_event(
        render.project_id,
        "hero-media.updated",
        {
            "render_id": str(render.id),
            "status": render.status,
            "status_detail": render.status_detail,
            "retry_count": render.retry_count,
            "progress_log": render.progress_log,
            "error": render.error,
        },
    )


def _ordered_assets(asset_ids: list[str], rows: list[HeroMediaAsset]) -> list[HeroMediaAsset]:
    row_by_id = {str(row.id): row for row in rows}
    return [row_by_id[asset_id] for asset_id in asset_ids if asset_id in row_by_id]


def _pick_asset(assets: list[HeroMediaAsset], index: int | None) -> HeroMediaAsset | None:
    if index is None:
        return assets[0] if assets else None
    if 0 <= index < len(assets):
        return assets[index]
    return None


def _locked_media_plan(render: HeroMediaRender) -> HeroMediaPlanKind:
    """Use the immutable choice captured when this render was queued."""
    return cast(HeroMediaPlanKind, render.media_plan)


async def run_hero_media_render(render_id: UUID) -> None:
    settings = get_settings()
    engine = get_engine()
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        render = await session.get(HeroMediaRender, render_id)
        if render is None:
            return
        brief = await session.get(HeroMediaBrief, render.brief_id)
        if brief is None:
            render.status = "failed"
            render.error = "hero-media plan not found"
            render.finished_at = _now()
            await session.commit()
            await _publish_status(render)
            return

        result = await session.execute(
            select(HeroMediaAsset).where(
                HeroMediaAsset.id.in_([UUID(value) for value in brief.asset_ids])
            )
        )
        source_assets = _ordered_assets(brief.asset_ids, list(result.scalars()))
        decision = HeroMediaDecision.model_validate(brief.plan)
        # A brief can be approved again after this render was queued. Retry the
        # original choice instead of silently turning motion into paid video.
        effective_plan = _locked_media_plan(render)

        _append_progress(render, status="rendering", detail="Готовлю постер и media-plan hero")
        render.started_at = _now()
        await session.commit()
        await _publish_status(render)

        primary_asset = _pick_asset(
            source_assets,
            decision.storyboard[0].primary_asset_index if decision.storyboard else None,
        )
        secondary_asset = _pick_asset(
            source_assets,
            decision.storyboard[0].secondary_asset_index if decision.storyboard else None,
        )
        shot = decision.storyboard[0] if decision.storyboard else None

        poster_asset: HeroMediaAsset | None = None
        if shot and shot.use_source_as_poster and primary_asset is not None:
            poster_asset = primary_asset

        if settings.hero_media_stub_mode:
            if poster_asset is None and primary_asset is None:
                raise RuntimeError("stub render needs at least one uploaded source asset")
            poster_asset = poster_asset or primary_asset
            _append_progress(
                render,
                status="assembling",
                detail="Stub-mode: vendor media calls simulated, poster взят из source asset",
            )
            await session.commit()
            await _publish_status(render)

            bundle = build_hero_bundle(
                decision=decision,
                mode=effective_plan,
                poster_url=poster_asset.storage_url,
                video_url=None,
            )
            render.poster_asset_id = poster_asset.id
            render.video_asset_id = None
            render.provider_summary = (
                "stub-mode: planner/render simulated; no real vendor media call"
            )
            render.bundle = bundle.model_dump()
            _append_progress(
                render,
                status="completed",
                detail=(
                    "Stub-mode hero готов: preview/apply path реальный, "
                    "media provider симулирован"
                ),
            )
            render.finished_at = _now()
            await session.commit()
            await _publish_status(render)
            return

        if poster_asset is None:
            poster_prompt = (
                shot.still_prompt
                if shot and shot.still_prompt
                else f"{decision.visual_style}. {brief.input_prompt}"
            )
            poster_url = await generate_and_store_image(str(render.project_id), poster_prompt)
            if not poster_url and primary_asset is not None:
                poster_asset = primary_asset
            elif not poster_url:
                raise RuntimeError(
                    "poster generation failed and no uploaded source asset is available"
                )
            else:
                poster_asset = HeroMediaAsset(
                    project_id=render.project_id,
                    owner_id=render.owner_id,
                    asset_role="poster",
                    media_kind="image",
                    storage_url=poster_url,
                    storage_key=_storage_key_from_url(poster_url, settings.minio_bucket_images),
                    mime_type=_guess_mime(poster_url, "image/webp"),
                    original_filename=None,
                    width=None,
                    height=None,
                    duration_ms=None,
                    bytes_size=None,
                    consent_confirmed=True,
                    moderation_status="unreviewed",
                    details={"generated": True, "provider": settings.image_gen_model},
                )
                session.add(poster_asset)
                await session.flush()

        video_asset: HeroMediaAsset | None = None
        if effective_plan in {"video", "cinematic"}:
            _append_progress(render, status="rendering", detail="Рендерю video hero")
            await session.commit()
            await _publish_status(render)
            motion_prompt = (
                shot.motion_prompt
                if shot and shot.motion_prompt
                else f"Slow premium camera move around {brief.input_prompt}"
            )
            video_error: str | None = None
            video_result: dict[str, Any] | None = None
            for attempt in range(2):
                video_result = await agent_media.generate_media(
                    str(render.project_id),
                    kind="video",
                    prompt=motion_prompt,
                    duration=5,
                    aspect="16:9",
                    first_frame=shot.first_frame_prompt if shot and primary_asset is None else None,
                    last_frame=shot.last_frame_prompt if shot and secondary_asset is None else None,
                    first_frame_url=primary_asset.storage_url
                    if primary_asset is not None
                    else None,
                    last_frame_url=secondary_asset.storage_url
                    if secondary_asset is not None
                    else None,
                )
                if video_result.get("ok"):
                    break
                render.retry_count += 1
                video_error = str(video_result.get("error") or "video generation failed")
                _append_progress(
                    render,
                    status="rendering",
                    detail=f"Видео не собрано, повтор {render.retry_count}: {video_error}",
                )
                await session.commit()
                await _publish_status(render)
                if attempt == 0:
                    await asyncio.sleep(1.0)
            if not video_result or not video_result.get("ok"):
                raise RuntimeError(video_error or "video generation failed")
            video_url = str(video_result["url"])
            video_asset = HeroMediaAsset(
                project_id=render.project_id,
                owner_id=render.owner_id,
                asset_role="clip",
                media_kind="video",
                storage_url=video_url,
                storage_key=_storage_key_from_url(video_url, settings.minio_bucket_videos),
                mime_type=_guess_mime(video_url, "video/mp4"),
                original_filename=None,
                width=None,
                height=None,
                duration_ms=5000,
                bytes_size=None,
                consent_confirmed=True,
                moderation_status="unreviewed",
                details={
                    "generated": True,
                    "provider": settings.video_gen_model,
                    "requested_plan": effective_plan,
                },
            )
            session.add(video_asset)
            await session.flush()

        _append_progress(render, status="assembling", detail="Собираю deterministic hero bundle")
        await session.commit()
        await _publish_status(render)

        bundle = build_hero_bundle(
            decision=decision,
            mode=effective_plan,
            poster_url=poster_asset.storage_url,
            video_url=video_asset.storage_url if video_asset is not None else None,
        )

        render.poster_asset_id = poster_asset.id
        render.video_asset_id = video_asset.id if video_asset is not None else None
        video_model = settings.video_gen_model if video_asset else "none"
        render.provider_summary = f"plan={effective_plan}; video_model={video_model}"
        render.bundle = bundle.model_dump()
        _append_progress(render, status="completed", detail="Hero готов")
        render.finished_at = _now()
        await session.commit()
        await _publish_status(render)


async def fail_hero_media_render(render_id: UUID, message: str) -> None:
    engine = get_engine()
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        render = await session.get(HeroMediaRender, render_id)
        if render is None:
            return
        render.status = "failed"
        render.error = message
        _append_progress(render, status="failed", detail="Сборка hero не завершилась")
        render.finished_at = _now()
        await session.commit()
        await _publish_status(render)
