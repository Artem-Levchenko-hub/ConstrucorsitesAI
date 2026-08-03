from __future__ import annotations

from uuid import UUID

from fastapi import status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.services import orchestrator_client
from omnia_api.services.deployment_state import deployment_is_active
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.runtime_sync import reconcile_locked_runtime


async def lock_project_mutation(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> Project:
    """Serialize a manual snapshot mutation against generation and deploy."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    project = (
        await session.execute(
            select(Project)
            .where(Project.id == project_id, Project.owner_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if project is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    active_generation = (
        await session.execute(
            select(GenerationRun.id).where(
                GenerationRun.project_id == project_id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if active_generation is not None:
        raise ApiError(
            "conflict",
            "Дождитесь завершения или отмены текущей генерации",
            status.HTTP_409_CONFLICT,
        )
    try:
        deployment = await orchestrator_client.get_deploy(project_id)
    except Exception as exc:
        raise ApiError(
            "deployment_state_unavailable",
            "Не удалось безопасно проверить публикацию проекта. Повторите позже.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if deployment_is_active(deployment):
        raise ApiError(
            "conflict",
            "Дождитесь завершения публикации",
            status.HTTP_409_CONFLICT,
        )
    if project.runtime_sync_required:
        await reconcile_locked_runtime(session, project, ensure_running=True)
    return project


__all__ = ["lock_project_mutation"]
