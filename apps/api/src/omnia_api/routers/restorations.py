from uuid import UUID

from fastapi import APIRouter, Response

from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers.snapshots import _public_dict
from omnia_api.schemas.restoration import (
    RestorationsPage,
    RestoreApplyRequest,
    RestoreOperation,
    RestoreRequest,
)
from omnia_api.schemas.snapshot import SnapshotPublic
from omnia_api.services import restorations
from omnia_api.services.restoration_runtime import HttpRestorationRuntime

router = APIRouter(prefix="/api/projects", tags=["restorations"])


async def _with_snapshot(session: SessionDep, operation: RestoreOperation) -> RestoreOperation:
    if operation.applied_snapshot_id is not None:
        snapshot = await session.get(Snapshot, operation.applied_snapshot_id)
        if snapshot is not None and snapshot.project_id == operation.project_id:
            return operation.model_copy(
                update={
                    "applied_snapshot": SnapshotPublic.model_validate(_public_dict(snapshot)),
                }
            )
    return operation


async def _enabled_owned(session: SessionDep, project_id: UUID, owner_id: UUID) -> None:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "project not found", 404)
    if not get_settings().max_code_restoration_enabled:
        raise ApiError("feature_disabled", "Version restoration is not enabled yet", 409)


@router.get("/{project_id}/restorations", response_model=RestorationsPage)
async def list_restorations(
    project_id: UUID,
    response: Response,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RestorationsPage:
    response.headers["Cache-Control"] = "no-store"
    return RestorationsPage(
        items=[
            await _with_snapshot(session, operation)
            for operation in await restorations.list_operations(
                session, project_id, current_user.id
            )
        ],
        enabled=get_settings().max_code_restoration_enabled,
    )


@router.post("/{project_id}/restorations", response_model=RestoreOperation)
async def prepare_restoration(
    project_id: UUID,
    body: RestoreRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RestoreOperation:
    await _enabled_owned(session, project_id, current_user.id)
    operation = await restorations.create_restoration(
        session,
        project_id,
        current_user.id,
        body,
        HttpRestorationRuntime(),
    )
    return await _with_snapshot(session, operation)


@router.get("/{project_id}/restorations/{operation_id}", response_model=RestoreOperation)
async def get_restoration(
    project_id: UUID,
    operation_id: UUID,
    response: Response,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RestoreOperation:
    response.headers["Cache-Control"] = "no-store"
    operation = await restorations.get_restoration(
        session,
        project_id,
        current_user.id,
        operation_id,
        HttpRestorationRuntime(),
        reconcile=get_settings().max_code_restoration_enabled,
    )
    return await _with_snapshot(session, operation)


@router.post("/{project_id}/restorations/{operation_id}/apply", response_model=RestoreOperation)
async def apply_restoration(
    project_id: UUID,
    operation_id: UUID,
    body: RestoreApplyRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RestoreOperation:
    await _enabled_owned(session, project_id, current_user.id)
    operation = await restorations.apply_restoration(
        session,
        project_id,
        current_user.id,
        operation_id,
        body,
        HttpRestorationRuntime(),
    )
    return await _with_snapshot(session, operation)


@router.post("/{project_id}/restorations/{operation_id}/cancel", response_model=RestoreOperation)
async def cancel_restoration(
    project_id: UUID,
    operation_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RestoreOperation:
    await _enabled_owned(session, project_id, current_user.id)
    operation = await restorations.cancel_restoration(
        session,
        project_id,
        current_user.id,
        operation_id,
        HttpRestorationRuntime(),
    )
    return await _with_snapshot(session, operation)
