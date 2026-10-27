"""Authenticated internal migration API. The API service authorizes the owner."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.core.internal_auth import verify_internal_token
from yleum_orchestrator.schemas.publication_migration import (
    PublicationMigrationConfirm,
    PublicationMigrationLatest,
    PublicationMigrationPrepare,
    PublicationMigrationStatus,
)
from yleum_orchestrator.services.publication_migration import get_publication_migration_service

router = APIRouter(prefix="/internal/projects", tags=["publication-migrations"])


def _conflict() -> OrchestratorError:
    return OrchestratorError(
        code="conflict", status_code=409, message="Состояние данных изменилось. Обновите страницу"
    )


@router.post("/{project_id}/publication-migrations", response_model=PublicationMigrationStatus)
async def prepare_migration(
    project_id: UUID,
    body: PublicationMigrationPrepare,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> PublicationMigrationStatus:
    verify_internal_token(x_internal_token)
    if body.publication.project_id != project_id:
        raise _conflict()
    try:
        return await get_publication_migration_service().prepare(
            body.publication, str(body.idempotency_key)
        )
    except CellResourceError:
        raise _conflict() from None


@router.get("/{project_id}/publication-migrations", response_model=PublicationMigrationLatest)
async def get_migration(
    project_id: UUID,
    owner_id: UUID,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> PublicationMigrationLatest:
    verify_internal_token(x_internal_token)
    try:
        return PublicationMigrationLatest(
            migration=get_publication_migration_service().get(project_id, owner_id)
        )
    except CellResourceError:
        raise _conflict() from None


@router.post(
    "/{project_id}/publication-migrations/{operation_id}/confirm",
    response_model=PublicationMigrationStatus,
)
async def confirm_migration(
    project_id: UUID,
    operation_id: UUID,
    body: PublicationMigrationConfirm,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> PublicationMigrationStatus:
    verify_internal_token(x_internal_token)
    if body.publication.project_id != project_id:
        raise _conflict()
    try:
        return await get_publication_migration_service().confirm(
            body.publication, operation_id, body.plan_sha256, body.explicit_intent
        )
    except CellResourceError:
        raise _conflict() from None
