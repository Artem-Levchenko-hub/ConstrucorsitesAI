"""Authenticated, fail-soft product advice for MAX Mini Apps."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import ValidationError
from sqlalchemy import select

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.redis import get_redis
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.product_advice import ProductAdviceItem, ProductAdviceResponse
from omnia_api.services import repo
from omnia_api.services.product_advisor import (
    ADVISOR_VERSION,
    SnapshotInput,
    build_advice_context,
    choose_analysis_snapshot,
    generate_product_advice,
)

router = APIRouter(prefix="/api/projects", tags=["product-advice"])
log = logging.getLogger(__name__)

_MODEL_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
_FALLBACK_CACHE_TTL_SECONDS = 15 * 60


def product_advice_cache_key(project_id: UUID, commit_sha: str) -> str:
    return f"omnia:product-advice:{ADVISOR_VERSION}:{project_id}:{commit_sha}"


async def _owned_max_project(
    session: SessionDep,
    project_id: UUID,
    owner_id: UUID,
) -> Project:
    project = (
        await session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.owner_id == owner_id,
            )
        )
    ).scalar_one_or_none()
    if project is None or project.template != "max_miniapp":
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


async def _snapshot_history(session: SessionDep, project: Project) -> list[Snapshot]:
    snapshots = list(
        (
            await session.execute(
                select(Snapshot)
                .where(Snapshot.project_id == project.id)
                .order_by(Snapshot.created_at.desc(), Snapshot.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    if project.current_snapshot_id is None or not snapshots:
        raise ApiError(
            "no_snapshot",
            "product advice is not ready",
            status.HTTP_409_CONFLICT,
        )
    current = next(
        (item for item in snapshots if item.id == project.current_snapshot_id),
        None,
    )
    if current is None:
        raise ApiError(
            "no_snapshot",
            "current snapshot not found",
            status.HTTP_409_CONFLICT,
        )
    return [current, *(item for item in snapshots if item.id != current.id)]


def _public_response(
    *,
    project: Project,
    current: Snapshot,
    analysis: Snapshot,
    archetype: str,
    source: str,
    items: tuple[object, ...],
) -> ProductAdviceResponse:
    return ProductAdviceResponse(
        version=ADVISOR_VERSION,
        project_id=project.id,
        current_snapshot_id=current.id,
        analysis_snapshot_id=analysis.id,
        archetype=archetype,
        source=source,
        items=[ProductAdviceItem.model_validate(item, from_attributes=True) for item in items],
    )


async def _read_cached(
    key: str,
    *,
    project: Project,
    current: Snapshot,
    analysis: Snapshot,
) -> ProductAdviceResponse | None:
    try:
        raw = await get_redis().get(key)
    except Exception:
        log.warning("product advice cache read failed", exc_info=True)
        return None
    if not raw:
        return None
    try:
        cached = ProductAdviceResponse.model_validate_json(raw)
    except ValidationError:
        log.warning("product advice cache payload rejected", exc_info=True)
        return None
    return cached.model_copy(
        update={
            "project_id": project.id,
            "current_snapshot_id": current.id,
            "analysis_snapshot_id": analysis.id,
            "source": "cache",
        }
    )


async def _write_cached(key: str, response: ProductAdviceResponse) -> None:
    ttl = _MODEL_CACHE_TTL_SECONDS if response.source == "model" else _FALLBACK_CACHE_TTL_SECONDS
    try:
        await get_redis().setex(key, ttl, response.model_dump_json())
    except Exception:
        log.warning("product advice cache write failed", exc_info=True)


@router.post("/{project_id}/product-advice", response_model=ProductAdviceResponse)
async def product_advice(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ProductAdviceResponse:
    project = await _owned_max_project(session, project_id, current_user.id)
    snapshots = await _snapshot_history(session, project)
    current = snapshots[0]
    selected = choose_analysis_snapshot(
        tuple(SnapshotInput(str(item.id), item.commit_sha, item.prompt_text) for item in snapshots)
    )
    analysis = next(item for item in snapshots if str(item.id) == selected.id)
    cache_key = product_advice_cache_key(project.id, analysis.commit_sha)
    cached = await _read_cached(
        cache_key,
        project=project,
        current=current,
        analysis=analysis,
    )
    if cached is not None:
        return cached

    files = await asyncio.to_thread(repo.read_files, project.id, analysis.commit_sha)
    context = build_advice_context(
        project_name=project.name,
        material_prompt=analysis.prompt_text or project.name,
        discovery_spec=project.discovery_spec,
        files=files,
    )
    result = await generate_product_advice(context)
    response = _public_response(
        project=project,
        current=current,
        analysis=analysis,
        archetype=result.archetype,
        source=result.source,
        items=result.items,
    )
    await _write_cached(cache_key, response)
    return response


__all__ = ["product_advice_cache_key", "router"]
