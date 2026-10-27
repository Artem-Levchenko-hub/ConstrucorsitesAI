"""Exact owner/candidate migration forwarding and shared admission exclusion."""

from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.schemas.publication_migrations import MigrationStatus
from yleum_api.services import orchestrator_client, project_cell_runtime
from yleum_api.services.cell_publication import load_publication_evidence


def checked_status(payload: object, project_id: UUID,
                   snapshot_id: UUID | None = None) -> MigrationStatus:
    try:
        result = MigrationStatus.model_validate(payload)
        if result.project_id != project_id or (
            snapshot_id is not None and result.snapshot_id != snapshot_id
        ):
            raise ValueError("migration identity mismatch")
        return result
    except (ValidationError, ValueError, TypeError):
        raise ApiError("orchestrator_unavailable", "Не удалось проверить изменение данных", 503
                       ) from None


async def get_status(project_id: UUID, owner_id: UUID) -> MigrationStatus | None:
    payload = await orchestrator_client.get_publication_migration(project_id, owner_id)
    if set(payload) != {"migration"}:
        raise ApiError("orchestrator_unavailable", "Не удалось проверить изменение данных", 503)
    value = payload["migration"]
    return None if value is None else checked_status(value, project_id)


def applied_to_snapshot(operation: MigrationStatus | None, project_id: UUID,
                        snapshot_id: UUID | None) -> bool:
    return bool(operation is not None and operation.project_id == project_id
                and operation.snapshot_id == snapshot_id and operation.state == "applied"
                and operation.backup_verified and operation.plan_sha256
                and not operation.effect_possible)


async def assert_no_active_migration(session: AsyncSession, project_id: UUID) -> None:
    """Caller holds the same project admission lock as generation/restoration."""
    workspace = await session.scalar(select(ProjectCellWorkspace).where(
        ProjectCellWorkspace.project_id == project_id,
        ProjectCellWorkspace.provider == "docker_owner_canary",
        ProjectCellWorkspace.deleted_at.is_(None),
    ))
    if workspace is None:
        return
    operation = await get_status(project_id, workspace.owner_id)
    if operation is not None and (
        operation.effect_possible or operation.state in {"preparing", "applying", "reconciling"}
    ):
        raise ApiError("publication_migration_active",
                       "Сначала дождитесь завершения проверки или изменения данных", 409)


async def publication_evidence(session: AsyncSession, project: Project,
                               owner: Any) -> dict[str, Any]:
    # Same ordering as generation admission: advisory project lock, then row lock.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
                          {"project_id": str(project.id)})
    await session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    await session.refresh(project)
    selection = await project_cell_runtime.resolve_project_cell_public_selection(
        session, project, owner=owner,
    )
    if not selection.selected or selection.workspace is None:
        raise ApiError("conflict", "Проверка изменения данных недоступна для этого приложения", 409)
    evidence = await load_publication_evidence(session, project, selection.workspace)
    if evidence.get("restoration_operation_id") is not None:
        raise ApiError("conflict", "Сначала завершите публикацию восстановленной версии", 409)
    # load_publication_evidence returns a safe projection without runtime secrets/config.
    evidence["idempotency_key"] = "migration-evidence-" + str(project.current_snapshot_id)
    return evidence
