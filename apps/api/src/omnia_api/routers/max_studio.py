"""Model-free MAX Studio configuration, managed kit and launch readiness."""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, status
from sqlalchemy import func, select, text

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.max_studio import (
    MaxLegal,
    MaxOperator,
    MaxProjectConfigPayload,
    MaxProjectConfigPublic,
    MaxReadinessItem,
    MaxReadinessPublic,
    MaxSupport,
    MaxUrlAttachedPayload,
)
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.max_project_kit import render_max_managed_files

router = APIRouter(prefix="/api/projects", tags=["max-studio"])
log = structlog.get_logger(__name__)


async def _owned_max_project(
    session: SessionDep, project_id: UUID, owner_id: UUID, *, lock: bool = False
) -> Project:
    statement = select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    project = (await session.execute(statement)).scalar_one_or_none()
    if project is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.template != "max_miniapp":
        raise ApiError(
            "max_project_required",
            "Настройки MAX доступны только для проекта MAX Mini App",
            status.HTTP_409_CONFLICT,
        )
    return project


def _default_config(project: Project) -> MaxProjectConfigPayload:
    return MaxProjectConfigPayload(
        app_name=project.name,
        app_type="custom",
        summary="Мини-приложение для пользователей MAX",
        operator=MaxOperator(),
        support=MaxSupport(),
        legal=MaxLegal(),
    )


def _public(
    project: Project, record: MaxProjectConfig | None
) -> MaxProjectConfigPublic:
    if record is None:
        return MaxProjectConfigPublic(
            project_id=project.id,
            config_version=0,
            config=_default_config(project),
        )
    return MaxProjectConfigPublic(
        project_id=project.id,
        config_version=record.config_version,
        config=MaxProjectConfigPayload.model_validate(record.config),
        synced_snapshot_id=record.synced_snapshot_id,
        updated_at=record.updated_at,
    )


@router.get("/{project_id}/max/config", response_model=MaxProjectConfigPublic)
async def get_max_config(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> MaxProjectConfigPublic:
    project = await _owned_max_project(session, project_id, current_user.id)
    record = await session.get(MaxProjectConfig, project_id)
    return _public(project, record)


@router.patch(
    "/{project_id}/max/config/url-attached",
    response_model=MaxProjectConfigPublic,
)
async def patch_max_url_attached(
    project_id: UUID,
    payload: MaxUrlAttachedPayload,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> MaxProjectConfigPublic:
    """Persist the manual MAX-cabinet confirmation without changing app code."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    project = await _owned_max_project(session, project_id, current_user.id, lock=True)
    record = await session.get(MaxProjectConfig, project_id)
    current = (
        MaxProjectConfigPayload.model_validate(record.config)
        if record is not None
        else _default_config(project)
    )
    if current.max_url_attached == payload.attached:
        return _public(project, record)

    updated = current.model_copy(update={"max_url_attached": payload.attached})
    if record is None:
        record = MaxProjectConfig(
            project_id=project.id,
            owner_id=current_user.id,
            config=updated.model_dump(mode="json"),
            config_version=1,
        )
        session.add(record)
    else:
        record.config = updated.model_dump(mode="json")
        record.config_version += 1
    await session.commit()
    await session.refresh(record)
    return _public(project, record)


@router.put("/{project_id}/max/config", response_model=MaxProjectConfigPublic)
async def put_max_config(
    project_id: UUID,
    payload: MaxProjectConfigPayload,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> MaxProjectConfigPublic:
    # Share the same per-project transaction lock as prompt reservation. This
    # prevents a no-code config commit and an LLM commit from branching off the
    # same parent and making one another disappear from the current timeline.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    project = await _owned_max_project(session, project_id, current_user.id, lock=True)
    active_generation = (
        await session.execute(
            select(GenerationRun.id).where(
                GenerationRun.project_id == project.id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if active_generation is not None:
        raise ApiError(
            "conflict",
            "Дождитесь завершения текущей сборки и сохраните настройки ещё раз",
            status.HTTP_409_CONFLICT,
        )
    try:
        deployment = await orchestrator_client.get_deploy(project.id)
    except Exception:
        deployment = {}
    if deployment.get("phase") in {"building", "pushing", "swapping", "cancelling"}:
        raise ApiError(
            "conflict",
            "Дождитесь завершения публикации и сохраните настройки ещё раз",
            status.HTTP_409_CONFLICT,
        )
    if project.current_snapshot_id is None:
        raise ApiError("project_empty", "У проекта нет исходного снимка", status.HTTP_409_CONFLICT)
    current = await session.get(Snapshot, project.current_snapshot_id)
    if current is None:
        raise ApiError(
            "project_empty",
            "Текущий снимок проекта не найден",
            status.HTTP_409_CONFLICT,
        )

    config_data = payload.model_dump(mode="json")
    record = await session.get(MaxProjectConfig, project_id)
    if (
        record is not None
        and record.config == config_data
        and record.synced_snapshot_id == project.current_snapshot_id
    ):
        return _public(project, record)

    files = render_max_managed_files(payload)
    commit_sha = await asyncio.to_thread(
        repo_svc.commit_files,
        project.id,
        files,
        "Update MAX business configuration",
        current.commit_sha,
    )
    snapshot = Snapshot(
        project_id=project.id,
        commit_sha=commit_sha,
        prompt_text=None,
        model_id=None,
        parent_id=current.id,
    )
    session.add(snapshot)
    await session.flush()
    project.current_snapshot_id = snapshot.id
    if record is None:
        record = MaxProjectConfig(
            project_id=project.id,
            owner_id=current_user.id,
            config=config_data,
            config_version=1,
        )
        session.add(record)
    else:
        record.config = config_data
        record.config_version += 1
    record.synced_snapshot_id = snapshot.id
    await session.commit()
    await session.refresh(record)

    # A stopped runtime will receive the canonical snapshot on the next start.
    # A live runtime can be updated immediately; a preview outage must not roll
    # back the safely persisted business configuration.
    try:
        runtime = await orchestrator_client.get_status(project.id)
        if runtime.get("state") == "running":
            await orchestrator_client.hot_reload(project.id, project.slug, files)
    except Exception:
        log.warning("max_config_live_sync_failed", project_id=str(project.id), exc_info=True)
    return _public(project, record)


@router.get("/{project_id}/max/readiness", response_model=MaxReadinessPublic)
async def get_max_readiness(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> MaxReadinessPublic:
    project = await _owned_max_project(session, project_id, current_user.id)
    record = await session.get(MaxProjectConfig, project_id)
    config = MaxProjectConfigPayload.model_validate(record.config) if record else None
    integration = (
        await session.execute(
            select(MaxIntegration).where(MaxIntegration.project_id == project.id)
        )
    ).scalar_one_or_none()
    generated_count = int(
        (
            await session.execute(
                select(func.count(Snapshot.id)).where(
                    Snapshot.project_id == project.id,
                    Snapshot.prompt_text.is_not(None),
                )
            )
        ).scalar_one()
    )
    try:
        deployment = await orchestrator_client.get_deploy(project.id)
    except Exception:
        deployment = {}
    deployed_at: datetime | None = None
    finished_at = deployment.get("finished_at")
    if isinstance(finished_at, str):
        try:
            deployed_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        except ValueError:
            deployed_at = None
    current_snapshot = (
        await session.get(Snapshot, project.current_snapshot_id)
        if project.current_snapshot_id
        else None
    )
    published = bool(
        deployment.get("phase") == "done"
        and deployment.get("prod_url")
        and deployed_at
        and current_snapshot
        and deployed_at >= current_snapshot.created_at
    )
    configured = bool(
        config
        and config.app_name
        and config.summary
        and config.primary_action
        and config.operator.legal_name
        and config.support.email
    )
    items = [
        MaxReadinessItem(
            id="business",
            label="Бизнес-профиль и поддержка заполнены",
            done=configured,
            action="Заполнить настройки",
        ),
        MaxReadinessItem(
            id="legal",
            label="Юридические данные подтверждены",
            done=bool(config and config.legal.terms_accepted),
            action="Подтвердить данные",
        ),
        MaxReadinessItem(
            id="build",
            label="Рабочая версия приложения собрана",
            done=generated_count > 0,
            action="Завершить сборку",
        ),
        MaxReadinessItem(
            id="bot",
            label="MAX-бот проверен",
            done=bool(integration and integration.verified_at),
            action="Подключить бота",
        ),
        MaxReadinessItem(
            id="publish",
            label="Текущая версия доступна по HTTPS",
            done=published,
            action="Опубликовать",
        ),
        MaxReadinessItem(
            id="webhook",
            label="Webhook MAX активирован",
            done=bool(integration and integration.status == "active"),
            action="Активировать webhook",
        ),
        MaxReadinessItem(
            id="max_url",
            label="URL приложения привязан в кабинете MAX",
            done=bool(config and config.max_url_attached),
            action="Подтвердить привязку",
        ),
    ]
    done = sum(item.done for item in items)
    return MaxReadinessPublic(
        ready_to_launch=done == len(items),
        progress=round(done / len(items) * 100),
        items=items,
    )
