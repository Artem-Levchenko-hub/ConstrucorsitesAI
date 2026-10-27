"""Explicit owner preparation/confirmation for one accepted generated candidate."""

from uuid import UUID

from fastapi import APIRouter

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.routers.max_studio import _owned_max_project
from yleum_api.schemas.publication_migrations import (
    MigrationConfirmation,
    MigrationPreparation,
    MigrationStatus,
)
from yleum_api.services import orchestrator_client
from yleum_api.services import publication_migrations as service

router = APIRouter(prefix="/api/projects", tags=["publication-migrations"])


@router.get("/{project_id}/max/publication-migrations", response_model=MigrationStatus | None)
async def status(project_id: UUID, session: SessionDep,
                 current_user: CurrentUserDep) -> MigrationStatus | None:
    await _owned_max_project(session, project_id, current_user.id)
    return await service.get_status(project_id, current_user.id)


@router.post("/{project_id}/max/publication-migrations", response_model=MigrationStatus)
async def prepare(project_id: UUID, body: MigrationPreparation, session: SessionDep,
                  current_user: CurrentUserDep) -> MigrationStatus:
    project = await _owned_max_project(session, project_id, current_user.id)
    evidence = await service.publication_evidence(session, project, current_user)
    payload = await orchestrator_client.prepare_publication_migration(project_id, {
        "publication": evidence, "idempotency_key": str(body.idempotency_key),
    })
    return service.checked_status(payload, project_id, project.current_snapshot_id)


@router.post("/{project_id}/max/publication-migrations/{operation_id}/confirm",
             response_model=MigrationStatus)
async def confirm(project_id: UUID, operation_id: UUID, body: MigrationConfirmation,
                  session: SessionDep, current_user: CurrentUserDep) -> MigrationStatus:
    project = await _owned_max_project(session, project_id, current_user.id)
    if body.explicit_intent is not True:
        raise ApiError("conflict", "Требуется отдельное подтверждение изменения данных", 409)
    evidence = await service.publication_evidence(session, project, current_user)
    payload = await orchestrator_client.confirm_publication_migration(project_id, operation_id, {
        "publication": evidence, "plan_sha256": body.plan_sha256, "explicit_intent": True,
    })
    result = service.checked_status(payload, project_id, project.current_snapshot_id)
    if result.operation_id != operation_id:
        raise ApiError("orchestrator_unavailable", "Не удалось проверить изменение данных", 503)
    return result
