"""Durable owner-project deletion: fence new work before releasing Cell compute."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.orchestrator_client import HttpProjectCellOrchestratorClient
from omnia_api.services.project_cell_lifecycle import (
    ProjectCellOperationOutcome,
    execute_cell_operation,
    reconcile_indeterminate_cell_operation,
    replay_indeterminate_cell_operation,
)
from omnia_api.services.project_cells import (
    ProjectCellBusy,
    _stored_request_payload,
    reserve_cell_operation,
)


async def teardown_project_cell(session: AsyncSession, project: Project) -> None:
    """Keep the project/tombstone on failure; retry the exact uncertain operation.

    Provider cleanup retains verified archives separately from compute. It also
    seals a controller tombstone, so in-flight requests cannot resurrect a Cell
    after PostgreSQL cascades the workspace row.
    """
    workspace = await session.scalar(select(ProjectCellWorkspace).where(
        ProjectCellWorkspace.project_id == project.id,
    ))
    if workspace is None:
        return
    if workspace.owner_id != project.owner_id or workspace.provider != "docker_owner_canary":
        raise ApiError("conflict", "Не удалось подтвердить владельца среды проекта", 409)
    expected_owner = project.owner_id
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project.id)},
    )
    current_project = await session.get(Project, project.id, populate_existing=True)
    if current_project is None:
        raise ApiError("not_found", "project not found", 404)
    if current_project.owner_id != expected_owner:
        raise ApiError("forbidden", "not your project", 403)
    active = await session.scalar(select(GenerationRun.id).where(
        GenerationRun.project_id == project.id,
        GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
    ).limit(1))
    if active is not None:
        raise ApiError("conflict", "Перед удалением проекта остановите активную сборку", 409)

    workspace_id = workspace.id
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:workspace_id))"),
        {"workspace_id": str(workspace_id)},
    )
    await session.refresh(workspace)
    if workspace.owner_id != expected_owner or workspace.project_id != project.id:
        raise ApiError("conflict", "Не удалось подтвердить владельца среды проекта", 409)
    workspace.state = "deleting"
    workspace.deleted_at = workspace.deleted_at or datetime.now(UTC)
    # Claiming work takes this same workspace lock. A pending request has not
    # dispatched; waiting_capacity is a confirmed pre-effect rejection. Neither
    # may later wake a deleted project. Never cancel running/uncertain effects.
    await session.execute(update(ProjectCellOperation).where(
        ProjectCellOperation.workspace_id == workspace_id,
        ProjectCellOperation.generation_run_id.is_(None),
        ProjectCellOperation.kind == "wake",
        ProjectCellOperation.status.in_(("pending", "waiting_capacity")),
    ).values(
        status="cancelled", finished_at=datetime.now(UTC),
        capacity_reason=None, next_attempt_at=None,
    ).execution_options(synchronize_session=False))
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    # The intent survives browser disconnect/API restart and denies preview wake.
    await session.commit()

    try:
        outcome = await _advance_deletion(session, factory, workspace_id)
    except ProjectCellBusy as exc:
        raise ApiError(
            "conflict", "Среда завершает предыдущую операцию. Повторите удаление проекта.", 409,
        ) from exc
    response = outcome.response
    if (
        outcome.kind != "destroy" or outcome.status != "completed"
        or response is None or response.state != "retained"
        or response.has_draft_runtime
        or (not response.checkpoint_ref and any((
            response.has_workspace, response.has_agent_home,
            response.has_postgres, response.has_redis,
        )))
    ):
        raise ApiError(
            "orchestrator_unavailable",
            "Удаление среды проекта ещё не подтверждено. Данные сохранены; повторите удаление.",
            503,
        )


async def _reserve_delete_step(
    session: AsyncSession, workspace_id: UUID, *, reconcile_after: UUID | None = None,
) -> ProjectCellOperation:
    """Serialize a durable chain; never supersede an uncertain effect blindly."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:workspace_id))"),
        {"workspace_id": str(workspace_id)},
    )
    prefix = f"project-delete:{workspace_id}"
    previous = await session.scalar(select(ProjectCellOperation).where(
        ProjectCellOperation.workspace_id == workspace_id,
        ProjectCellOperation.kind.in_(("destroy", "reconcile")),
        ProjectCellOperation.idempotency_key.startswith(prefix),
    ).order_by(
        ProjectCellOperation.created_at.desc(), ProjectCellOperation.id.desc(),
    ).limit(1).execution_options(populate_existing=True))
    if previous is not None and (
        (reconcile_after is not None and previous.id != reconcile_after)
        or previous.status in {"pending", "running", "waiting_capacity"}
        or (previous.kind == "destroy" and previous.status == "completed")
        or (
            previous.kind == "destroy" and previous.status == "indeterminate"
            and reconcile_after is None
        )
    ):
        await session.commit()
        return previous

    target: UUID | None = None
    if previous is None:
        # An interrupted owner wake may predate the delete request. Observe its
        # current fence first; older uncertainties superseded by later operations
        # must not be replayed or used to restore old data.
        latest = await session.scalar(select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace_id,
            ProjectCellOperation.fencing_epoch.is_not(None),
        ).order_by(ProjectCellOperation.fencing_epoch.desc()).limit(1))
        if latest is not None and latest.status == "indeterminate":
            target = latest.id
    elif previous.status == "indeterminate":
        target = previous.id
    elif previous.kind == "reconcile" and previous.status in {"failed", "cancelled"}:
        target = UUID(str(_stored_request_payload(previous)["indeterminate_operation_id"]))

    kind = "reconcile" if target is not None else "destroy"
    anchor = previous.id if previous is not None else target
    key = prefix if anchor is None else f"{prefix}:{kind}:{anchor}"
    operation, _ = await reserve_cell_operation(
        session, workspace_id=workspace_id, generation_run_id=None,
        kind=kind, idempotency_key=key,
        request={} if target is None else {"indeterminate_operation_id": str(target)},
    )
    await session.commit()
    return operation


async def _advance_deletion(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], workspace_id: UUID,
) -> ProjectCellOperationOutcome:
    """At most replay -> observe -> destroy; every step survives a restart."""
    client = HttpProjectCellOrchestratorClient()
    operation = await _reserve_delete_step(session, workspace_id)
    for step in range(3):
        replay = operation.kind == "destroy" and operation.status == "indeterminate"
        if operation.kind == "reconcile":
            target = UUID(str(_stored_request_payload(operation)["indeterminate_operation_id"]))
            outcome = await reconcile_indeterminate_cell_operation(
                factory, target, operation.id, client,
            )
        else:
            outcome = await (
                replay_indeterminate_cell_operation(factory, operation.id, client)
                if replay else execute_cell_operation(factory, operation.id, client)
            )
        if step == 2:
            return outcome
        if outcome.kind == "reconcile" and outcome.status == "completed":
            operation = await _reserve_delete_step(session, workspace_id)
        elif replay and outcome.status == "indeterminate":
            operation = await _reserve_delete_step(
                session, workspace_id, reconcile_after=outcome.operation_id,
            )
        else:
            return outcome
    raise AssertionError("bounded deletion did not produce an outcome")
