"""Authenticated contextual product advice for MAX Mini Apps."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import ValidationError
from sqlalchemy import select

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.redis import get_redis
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.product_advice import ProductAdviceItem, ProductAdviceResponse
from omnia_api.services import repo
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.product_advisor import (
    ADVISOR_VERSION,
    advice_unavailable,
    build_advice_context,
    generate_product_advice,
)

router = APIRouter(prefix="/api/projects", tags=["product-advice"])
log = logging.getLogger(__name__)

_MODEL_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
_IN_FLIGHT: dict[str, asyncio.Task[ProductAdviceResponse]] = {}


def product_advice_cache_key(project_id: UUID, commit_sha: str, configuration: str = "") -> str:
    return f"omnia:product-advice:{ADVISOR_VERSION}:{project_id}:{commit_sha}:{configuration}"


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
    by_id = {item.id: item for item in snapshots}
    ancestry = [current]
    seen = {current.id}
    while ancestry[-1].parent_id in by_id:
        parent = by_id[ancestry[-1].parent_id]
        if parent.id in seen:
            break
        ancestry.append(parent)
        seen.add(parent.id)
    return ancestry


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
    if (
        cached.version != ADVISOR_VERSION
        or cached.project_id != project.id
        or cached.current_snapshot_id != current.id
        or cached.analysis_snapshot_id != analysis.id
        or cached.source != "model"
    ):
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
    try:
        await get_redis().setex(key, _MODEL_CACHE_TTL_SECONDS, response.model_dump_json())
    except Exception:
        log.warning("product advice cache write failed", exc_info=True)


def _release_analysis(key: str, task: asyncio.Task[ProductAdviceResponse]) -> None:
    if _IN_FLIGHT.get(key) is task:
        _IN_FLIGHT.pop(key, None)
    # Retrieve errors even when the requesting browser disconnects.
    if not task.cancelled():
        task.exception()


async def _analyze(
    project: Project,
    snapshots: list[Snapshot],
    app_config: dict[str, object],
    cache_key: str,
) -> ProductAdviceResponse:
    current = snapshots[0]
    cached = await _read_cached(
        cache_key,
        project=project,
        current=current,
        analysis=current,
    )
    if cached is not None:
        return cached
    try:
        files = await asyncio.to_thread(repo.read_files, project.id, current.commit_sha)
    except Exception as exc:
        raise advice_unavailable() from exc
    initial_brief = next(
        (item.prompt_text for item in reversed(snapshots) if item.prompt_text),
        project.name,
    )
    context = build_advice_context(
        project_name=project.name,
        material_prompt=current.prompt_text or project.name,
        initial_brief=initial_brief,
        recent_changes=tuple(item.prompt_text for item in snapshots[:5] if item.prompt_text),
        discovery_spec=project.discovery_spec,
        app_config=app_config,
        files=files,
    )
    result = await generate_product_advice(context)
    response = _public_response(
        project=project,
        current=current,
        analysis=current,
        archetype=result.archetype,
        source=result.source,
        items=result.items,
    )
    await _write_cached(cache_key, response)
    return response


@router.post("/{project_id}/product-advice", response_model=ProductAdviceResponse)
async def product_advice(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ProductAdviceResponse:
    project = await _owned_max_project(session, project_id, current_user.id)
    active_run = await session.scalar(
        select(GenerationRun.id)
        .where(
            GenerationRun.project_id == project.id,
            GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
        )
        .limit(1)
    )
    if active_run is not None:
        raise ApiError(
            "conflict",
            "Дождитесь завершения текущего изменения приложения.",
            409,
        )
    snapshots = await _snapshot_history(session, project)
    current = snapshots[0]
    record = await session.get(MaxProjectConfig, project.id)
    app_config = record.config if record else {}
    # Hash settings locally; personal/legal records never become model context.
    configuration = hashlib.sha256(
        json.dumps(
            {
                "snapshot_id": str(current.id),
                "name": project.name,
                "discovery": project.discovery_spec,
                "config": app_config,
                "runtime_ai_enabled": project.runtime_ai_enabled,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()[:24]
    cache_key = product_advice_cache_key(project.id, current.commit_sha, configuration)
    cached = await _read_cached(
        cache_key,
        project=project,
        current=current,
        analysis=current,
    )
    if cached is not None:
        return cached
    # Share work across tabs in this API process, including Redis outages.
    task = _IN_FLIGHT.get(cache_key)
    if task is None:
        task = asyncio.create_task(_analyze(project, snapshots, app_config, cache_key))
        _IN_FLIGHT[cache_key] = task
        task.add_done_callback(lambda done: _release_analysis(cache_key, done))
    return await asyncio.shield(task)


__all__ = ["product_advice_cache_key", "router"]
