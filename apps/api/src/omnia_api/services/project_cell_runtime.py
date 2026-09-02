from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.schemas.runtime import RuntimeStatus
from omnia_api.services import orchestrator_client
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.project_cell_access import decide_project_cell_access

_CELL_ACTION_UNAVAILABLE = (
    "Для owner-only Project Cell это действие пока недоступно в публичном runtime"
)
_CELL_FIRST_BUILD_REQUIRED = (
    "MAX preview появится после первой кодовой сборки проекта"
)
_CELL_LEASE_MISSING = (
    "Безопасная preview-сессия Project Cell потеряна; запустите сборку ещё раз"
)
_CELL_OWNERSHIP_MISMATCH = "Project Cell workspace identity mismatch"


@dataclass(frozen=True, slots=True)
class ProjectCellPublicSelection:
    selected: bool
    source: Literal["legacy", "durable_workspace", "owner_canary"]
    owner: User
    workspace: ProjectCellWorkspace | None


async def resolve_project_cell_public_selection(
    session: AsyncSession,
    project: Project,
    *,
    owner: User | None = None,
    populate_existing: bool = False,
) -> ProjectCellPublicSelection:
    resolved_owner = owner or await session.get(User, project.owner_id)
    if resolved_owner is None:
        raise ApiError("not_found", "project owner not found", 404)

    statement = (
        select(ProjectCellWorkspace)
        .where(ProjectCellWorkspace.project_id == project.id)
        .order_by(ProjectCellWorkspace.created_at.desc())
        .limit(1)
    )
    if populate_existing:
        statement = statement.execution_options(populate_existing=True)
    workspace = await session.scalar(statement)
    if workspace is not None and workspace.provider == "docker_owner_canary":
        return ProjectCellPublicSelection(
            selected=True,
            source="durable_workspace",
            owner=resolved_owner,
            workspace=workspace,
        )

    access = decide_project_cell_access(resolved_owner)
    if (
        project.template == "max_miniapp"
        and access.enabled
        and access.provider == "docker_owner_canary"
    ):
        return ProjectCellPublicSelection(
            selected=True,
            source="owner_canary",
            owner=resolved_owner,
            workspace=None,
        )

    return ProjectCellPublicSelection(
        selected=False,
        source="legacy",
        owner=resolved_owner,
        workspace=workspace,
    )


async def load_project_cell_runtime_status(
    session: AsyncSession,
    project: Project,
    *,
    owner: User | None = None,
) -> RuntimeStatus | None:
    selection = await resolve_project_cell_public_selection(session, project, owner=owner)
    if not selection.selected:
        return None
    active_generation = await _active_generation(session, project.id)
    if selection.workspace is None:
        return _pending_status(active_generation is not None)
    _require_workspace_identity(project, selection)
    resources = await _get_cell_resources(selection.workspace.id)
    return _runtime_status_from_resources(
        selection.workspace,
        resources,
        active_generation=active_generation is not None,
    )


async def start_project_cell_runtime(
    session: AsyncSession,
    project: Project,
    *,
    owner: User | None = None,
) -> RuntimeStatus | None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project.id)},
    )
    locked_project = await session.get(Project, project.id, populate_existing=True)
    if locked_project is None:
        raise ApiError("not_found", "project not found", 404)
    selection = await resolve_project_cell_public_selection(
        session,
        locked_project,
        owner=owner,
        populate_existing=True,
    )
    if not selection.selected:
        return None
    active_generation = await _active_generation(session, locked_project.id)
    if selection.workspace is None:
        if active_generation is not None:
            return _pending_status(True)
        raise ApiError("conflict", _CELL_FIRST_BUILD_REQUIRED, 409)
    _require_workspace_identity(locked_project, selection)
    resources = await _get_cell_resources(selection.workspace.id)
    status = _runtime_status_from_resources(
        selection.workspace,
        resources,
        active_generation=active_generation is not None,
    )
    if status.state == "running":
        return status
    if active_generation is not None:
        return _pending_status(True, workspace=selection.workspace, resources=resources)
    generation_run_id, fencing_epoch = require_project_cell_runtime_lease(selection.workspace)
    snapshot = await orchestrator_client.project_cell_agent_bootstrap(
        selection.workspace.id,
        generation_run_id=generation_run_id,
        fencing_epoch=fencing_epoch,
    )
    if (
        snapshot.generation_run_id != generation_run_id
        or snapshot.fencing_epoch != fencing_epoch
    ):
        raise orchestrator_client.OrchestratorUnavailable(
            "Orchestrator returned an invalid Project Cell bootstrap"
        )
    draft = await orchestrator_client.project_cell_apply_draft(
        selection.workspace.id,
        generation_run_id=generation_run_id,
        fencing_epoch=fencing_epoch,
        expected_revision=snapshot.workspace_revision,
        files={},
        deletes=(),
    )
    return RuntimeStatus(
        state="running" if draft.migration_exit_code in {None, 0} else "failed",
        container_name=_public_cell_ref(selection.workspace),
        port=None,
        dev_url=draft.preview_url,
        last_active_at=None,
        hibernate_after_seconds=None,
        keep_alive=False,
    )


async def create_project_cell_preview_session(
    session: AsyncSession,
    project: Project,
    *,
    owner: User | None = None,
) -> orchestrator_client.ProjectCellPreviewSession | None:
    selection = await resolve_project_cell_public_selection(session, project, owner=owner)
    if not selection.selected:
        return None
    active_generation = await _active_generation(session, project.id)
    if selection.workspace is None:
        if active_generation is not None:
            raise ApiError("conflict", "MAX preview ещё готовится", 409)
        raise ApiError("conflict", _CELL_FIRST_BUILD_REQUIRED, 409)
    _require_workspace_identity(project, selection)
    generation_run_id, fencing_epoch = require_project_cell_runtime_lease(selection.workspace)
    return await orchestrator_client.project_cell_create_preview_session(
        selection.workspace.id,
        generation_run_id=generation_run_id,
        fencing_epoch=fencing_epoch,
    )


def require_project_cell_runtime_lease(workspace: ProjectCellWorkspace) -> tuple[UUID, int]:
    if workspace.generation_run_id is None or workspace.fencing_epoch <= 0:
        raise ApiError("conflict", _CELL_LEASE_MISSING, 409)
    return workspace.generation_run_id, workspace.fencing_epoch


def raise_project_cell_public_action_unavailable() -> None:
    raise ApiError("conflict", _CELL_ACTION_UNAVAILABLE, 409)


async def _active_generation(
    session: AsyncSession,
    project_id: UUID,
) -> GenerationRun | None:
    return cast(
        GenerationRun | None,
        await session.scalar(
            select(GenerationRun)
            .where(
                GenerationRun.project_id == project_id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
        ),
    )


async def _get_cell_resources(
    workspace_id: UUID,
) -> orchestrator_client.ProjectCellResourceResponse:
    payload = await orchestrator_client._request(
        "GET",
        f"/internal/workspaces/{workspace_id}/resources",
    )
    response = orchestrator_client.ProjectCellResourceResponse.from_json(payload)
    if response.workspace_id != workspace_id:
        raise orchestrator_client.OrchestratorUnavailable(
            "Orchestrator returned an invalid Project Cell resource object"
        )
    return response


def _require_workspace_identity(
    project: Project,
    selection: ProjectCellPublicSelection,
) -> None:
    workspace = selection.workspace
    if (
        workspace is None
        or workspace.project_id != project.id
        or workspace.owner_id != project.owner_id
        or workspace.owner_id != selection.owner.id
    ):
        raise ApiError("conflict", _CELL_OWNERSHIP_MISMATCH, 409)


def _runtime_status_from_resources(
    workspace: ProjectCellWorkspace,
    resources: orchestrator_client.ProjectCellResourceResponse,
    *,
    active_generation: bool,
) -> RuntimeStatus:
    if resources.state == "resources_ready" and resources.draft_state == "running":
        return RuntimeStatus(
            state="running",
            container_name=_public_cell_ref(workspace),
            port=None,
            dev_url=resources.preview_url,
            last_active_at=None,
            hibernate_after_seconds=None,
            keep_alive=False,
        )
    if active_generation:
        return _pending_status(True, workspace=workspace, resources=resources)
    if resources.state in {"resources_ready", "resources_paused", "retained"}:
        state: Literal["stopped", "failed"] = (
            "failed" if resources.draft_state == "failed" else "stopped"
        )
        return RuntimeStatus(
            state=state,
            container_name=_public_cell_ref(workspace),
            port=None,
            dev_url=resources.preview_url if state == "failed" else None,
            last_active_at=None,
            hibernate_after_seconds=None,
            keep_alive=False,
        )
    return RuntimeStatus(
        state="failed",
        container_name=_public_cell_ref(workspace),
        port=None,
        dev_url=resources.preview_url,
        last_active_at=None,
        hibernate_after_seconds=None,
        keep_alive=False,
    )


def _pending_status(
    active_generation: bool,
    *,
    workspace: ProjectCellWorkspace | None = None,
    resources: orchestrator_client.ProjectCellResourceResponse | None = None,
) -> RuntimeStatus:
    return RuntimeStatus(
        state="provisioning" if active_generation else "stopped",
        container_name=_public_cell_ref(workspace) if workspace is not None else None,
        port=None,
        dev_url=(
            resources.preview_url
            if resources is not None and resources.draft_state == "running"
            else None
        ),
        last_active_at=None,
        hibernate_after_seconds=None,
        keep_alive=False,
    )


def _public_cell_ref(workspace: ProjectCellWorkspace) -> str:
    return workspace.provider_ref or f"project-cell-{workspace.id.hex[:12]}"
