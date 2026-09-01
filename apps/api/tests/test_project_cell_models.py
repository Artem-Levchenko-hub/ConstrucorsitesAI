from __future__ import annotations

import uuid
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, Column, DefaultClause, Table, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User

pytestmark = pytest.mark.asyncio

WORKSPACE_STATES = (
    "provisioning",
    "ready",
    "stopped",
    "failed",
    "deleting",
    "deleted",
)
OPERATION_KINDS = (
    "ensure",
    "wake",
    "pause",
    "stop",
    "destroy",
    "status",
    "restore",
    "reconcile",
)
OPERATION_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "indeterminate",
)


def _check_expressions(table: Table) -> dict[str, str]:
    return {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _server_default_sql(column: Column[object]) -> str | None:
    default = column.server_default
    if default is None:
        return None
    assert isinstance(default, DefaultClause)
    return str(default.arg)


async def _new_user(session: AsyncSession, label: str) -> User:
    user = User(email=f"project-cell-{label}-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _new_project(session: AsyncSession, owner: User) -> Project:
    project = Project(
        owner_id=owner.id,
        name="Project Cell model test",
        slug=f"project-cell-{uuid.uuid4().hex}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    return project


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    return await _new_user(db_session, "owner")


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, user: User) -> Project:
    return await _new_project(db_session, user)


@pytest_asyncio.fixture
async def project_cell_workspace(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> ProjectCellWorkspace:
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        provider="docker_owner_canary",
        state="provisioning",
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


# Mutation caught: adding, removing, or renaming a public persistence attribute.
async def test_project_cell_models_expose_exact_public_columns() -> None:
    assert set(ProjectCellWorkspace.__table__.columns.keys()) == {
        "id",
        "project_id",
        "owner_id",
        "provider",
        "provider_ref",
        "state",
        "generation_run_id",
        "provider_metadata",
        "fencing_epoch",
        "version",
        "last_error",
        "created_at",
        "updated_at",
        "ready_at",
        "deleted_at",
    }
    assert set(ProjectCellOperation.__table__.columns.keys()) == {
        "id",
        "workspace_id",
        "generation_run_id",
        "idempotency_key",
        "request_digest",
        "fencing_epoch",
        "kind",
        "status",
        "request_payload",
        "result_payload",
        "error",
        "created_at",
        "started_at",
        "finished_at",
    }


# Mutations caught: weakening named checks, FK delete actions, defaults, or partial uniqueness.
async def test_project_cell_metadata_matches_the_durable_contract() -> None:
    workspace = cast(Table, ProjectCellWorkspace.__table__)
    operation = cast(Table, ProjectCellOperation.__table__)

    assert _check_expressions(workspace) == {
        "ck_project_cell_workspaces_state_allowed": (
            "state IN ('provisioning', 'ready', 'stopped', 'failed', 'deleting', 'deleted')"
        )
    }
    assert _check_expressions(operation) == {
        "ck_project_cell_operations_kind_allowed": (
            "kind IN ('ensure', 'wake', 'pause', 'stop', 'destroy', 'status', "
            "'restore', 'reconcile')"
        ),
        "ck_project_cell_operations_status_allowed": (
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', "
            "'indeterminate')"
        ),
    }
    assert {constraint.name for constraint in workspace.constraints} >= {
        "uq_project_cell_workspaces_project_id"
    }
    assert {constraint.name for constraint in operation.constraints} >= {
        "uq_project_cell_operations_workspace_id_idempotency_key"
    }

    workspace_fks = {
        foreign_key.parent.name: (foreign_key.column.table.name, foreign_key.ondelete)
        for foreign_key in workspace.foreign_keys
    }
    operation_fks = {
        foreign_key.parent.name: (foreign_key.column.table.name, foreign_key.ondelete)
        for foreign_key in operation.foreign_keys
    }
    assert workspace_fks == {
        "project_id": ("projects", "CASCADE"),
        "owner_id": ("users", "CASCADE"),
        "generation_run_id": ("generation_runs", "SET NULL"),
    }
    assert operation_fks == {
        "workspace_id": ("project_cell_workspaces", "CASCADE"),
        "generation_run_id": ("generation_runs", "SET NULL"),
    }

    active = next(
        index
        for index in operation.indexes
        if index.name == "uq_project_cell_operations_one_active_per_workspace"
    )
    assert active.unique is True
    assert [column.name for column in active.columns] == ["workspace_id"]
    assert str(active.dialect_options["postgresql"]["where"]) == (
        "status IN ('pending', 'running')"
    )

    assert _server_default_sql(workspace.c.provider_metadata) == "{}"
    assert _server_default_sql(operation.c.request_payload) == "{}"
    assert _server_default_sql(workspace.c.fencing_epoch) == "0"
    assert _server_default_sql(workspace.c.version) == "1"
    assert _server_default_sql(operation.c.status) == "pending"
    assert _server_default_sql(operation.c.fencing_epoch) is None
    assert operation.c.fencing_epoch.nullable is True


# Mutation caught: reusing one process-wide dict for either durable JSON payload.
async def test_json_payload_defaults_are_not_shared() -> None:
    workspace_one = ProjectCellWorkspace(
        project_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        provider="docker_owner_canary",
        state="provisioning",
    )
    workspace_two = ProjectCellWorkspace(
        project_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        provider="docker_owner_canary",
        state="provisioning",
    )
    one = ProjectCellOperation(
        workspace_id=uuid.uuid4(),
        kind="ensure",
        idempotency_key="ensure:run-one",
        request_digest="0" * 64,
    )
    two = ProjectCellOperation(
        workspace_id=uuid.uuid4(),
        kind="status",
        idempotency_key="status:run-two",
        request_digest="1" * 64,
    )

    assert workspace_one.provider_metadata == {}
    assert workspace_two.provider_metadata == {}
    assert workspace_one.provider_metadata is not workspace_two.provider_metadata
    assert one.request_payload == {}
    assert two.request_payload == {}
    assert one.request_payload is not two.request_payload


# Mutation caught: dropping the one-workspace-per-project unique constraint.
async def test_workspace_is_unique_per_project(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> None:
    first = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        provider="docker_owner_canary",
        state="provisioning",
    )
    db_session.add(first)
    await db_session.flush()
    db_session.add(
        ProjectCellWorkspace(
            project_id=project.id,
            owner_id=user.id,
            provider="docker_owner_canary",
            state="provisioning",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


# Mutation caught: dropping workspace-scoped operation idempotency.
async def test_operation_key_is_unique_per_workspace(
    db_session: AsyncSession,
    project_cell_workspace: ProjectCellWorkspace,
) -> None:
    db_session.add(
        ProjectCellOperation(
            workspace_id=project_cell_workspace.id,
            kind="ensure",
            status="completed",
            idempotency_key="ensure:same-key",
            request_digest="0" * 64,
        )
    )
    await db_session.flush()
    db_session.add(
        ProjectCellOperation(
            workspace_id=project_cell_workspace.id,
            kind="status",
            status="completed",
            idempotency_key="ensure:same-key",
            request_digest="1" * 64,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


# Mutation caught: removing pending/running from the one-active-operation predicate.
async def test_only_one_active_operation_is_allowed_per_workspace(
    db_session: AsyncSession,
    project_cell_workspace: ProjectCellWorkspace,
) -> None:
    db_session.add(
        ProjectCellOperation(
            workspace_id=project_cell_workspace.id,
            kind="ensure",
            status="pending",
            idempotency_key="ensure:pending",
            request_digest="0" * 64,
        )
    )
    await db_session.flush()
    db_session.add(
        ProjectCellOperation(
            workspace_id=project_cell_workspace.id,
            kind="status",
            status="running",
            idempotency_key="status:running",
            request_digest="1" * 64,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


# Mutation caught: making the partial active index cover terminal records.
async def test_terminal_operations_can_coexist_per_workspace(
    db_session: AsyncSession,
    project_cell_workspace: ProjectCellWorkspace,
) -> None:
    db_session.add_all(
        [
            ProjectCellOperation(
                workspace_id=project_cell_workspace.id,
                kind="ensure",
                status="completed",
                idempotency_key="ensure:completed",
                request_digest="0" * 64,
            ),
            ProjectCellOperation(
                workspace_id=project_cell_workspace.id,
                kind="status",
                status="failed",
                idempotency_key="status:failed",
                request_digest="1" * 64,
            ),
        ]
    )

    await db_session.flush()


# Mutation caught: omitting any required workspace state from the check expression.
async def test_all_workspace_states_are_accepted(
    db_session: AsyncSession,
    user: User,
) -> None:
    for state in WORKSPACE_STATES:
        project = await _new_project(db_session, user)
        db_session.add(
            ProjectCellWorkspace(
                project_id=project.id,
                owner_id=user.id,
                provider="docker_owner_canary",
                state=state,
            )
        )
    await db_session.flush()

    states = set(await db_session.scalars(select(ProjectCellWorkspace.state)))
    assert states == set(WORKSPACE_STATES)


# Mutation caught: omitting any required operation kind from the check expression.
async def test_all_operation_kinds_are_accepted(
    db_session: AsyncSession,
    project_cell_workspace: ProjectCellWorkspace,
) -> None:
    db_session.add_all(
        [
            ProjectCellOperation(
                workspace_id=project_cell_workspace.id,
                kind=kind,
                status="completed",
                idempotency_key=f"{kind}:allowed",
                request_digest=str(position) * 64,
            )
            for position, kind in enumerate(OPERATION_KINDS, start=1)
        ]
    )
    await db_session.flush()

    kinds = set(await db_session.scalars(select(ProjectCellOperation.kind)))
    assert kinds == set(OPERATION_KINDS)


# Mutation caught: omitting any required operation status from the check expression.
async def test_all_operation_statuses_are_accepted(
    db_session: AsyncSession,
    user: User,
) -> None:
    for position, status in enumerate(OPERATION_STATUSES, start=1):
        project = await _new_project(db_session, user)
        workspace = ProjectCellWorkspace(
            project_id=project.id,
            owner_id=user.id,
            provider="docker_owner_canary",
            state="ready",
        )
        db_session.add(workspace)
        await db_session.flush()
        db_session.add(
            ProjectCellOperation(
                workspace_id=workspace.id,
                kind="status",
                status=status,
                idempotency_key=f"status:{status}",
                request_digest=str(position) * 64,
            )
        )
    await db_session.flush()

    statuses = set(await db_session.scalars(select(ProjectCellOperation.status)))
    assert statuses == set(OPERATION_STATUSES)


# Mutation caught: admitting any value outside the exact workspace-state contract.
async def test_workspace_rejects_extra_states(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> None:
    for state in ("", "paused", "READY"):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(
                    ProjectCellWorkspace(
                        project_id=project.id,
                        owner_id=user.id,
                        provider="docker_owner_canary",
                        state=state,
                    )
                )
                await db_session.flush()


# Mutation caught: admitting any value outside the exact operation-kind contract.
async def test_operation_rejects_extra_kinds(
    db_session: AsyncSession,
    project_cell_workspace: ProjectCellWorkspace,
) -> None:
    for kind in ("", "execute", "restart"):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(
                    ProjectCellOperation(
                        workspace_id=project_cell_workspace.id,
                        kind=kind,
                        status="completed",
                        idempotency_key=f"{kind}:extra",
                        request_digest="0" * 64,
                    )
                )
                await db_session.flush()


# Mutation caught: admitting any value outside the exact operation-status contract.
async def test_operation_rejects_extra_statuses(
    db_session: AsyncSession,
    project_cell_workspace: ProjectCellWorkspace,
) -> None:
    for status in ("", "unknown", "succeeded"):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(
                    ProjectCellOperation(
                        workspace_id=project_cell_workspace.id,
                        kind="ensure",
                        status=status,
                        idempotency_key=f"status:{status}",
                        request_digest="0" * 64,
                    )
                )
                await db_session.flush()


# Mutation caught: changing projects.id ON DELETE from CASCADE.
async def test_deleting_project_cascades_workspace_and_operations(
    db_session: AsyncSession,
    project: Project,
    project_cell_workspace: ProjectCellWorkspace,
) -> None:
    operation = ProjectCellOperation(
        workspace_id=project_cell_workspace.id,
        kind="ensure",
        status="completed",
        idempotency_key="ensure:project-delete",
        request_digest="0" * 64,
    )
    db_session.add(operation)
    await db_session.flush()
    workspace_id = project_cell_workspace.id
    operation_id = operation.id

    await db_session.execute(delete(Project).where(Project.id == project.id))
    await db_session.flush()

    assert (
        await db_session.scalar(
            select(ProjectCellWorkspace.id).where(ProjectCellWorkspace.id == workspace_id)
        )
        is None
    )
    assert (
        await db_session.scalar(
            select(ProjectCellOperation.id).where(ProjectCellOperation.id == operation_id)
        )
        is None
    )


# Mutation caught: changing users.id ON DELETE from CASCADE on the workspace owner FK.
async def test_deleting_cell_owner_cascades_workspace_independently_of_project(
    db_session: AsyncSession,
) -> None:
    project_owner = await _new_user(db_session, "project-owner")
    cell_owner = await _new_user(db_session, "cell-owner")
    project = await _new_project(db_session, project_owner)
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=cell_owner.id,
        provider="docker_owner_canary",
        state="ready",
    )
    db_session.add(workspace)
    await db_session.flush()
    workspace_id = workspace.id

    await db_session.execute(delete(User).where(User.id == cell_owner.id))
    await db_session.flush()

    assert await db_session.scalar(select(Project.id).where(Project.id == project.id)) == project.id
    assert (
        await db_session.scalar(
            select(ProjectCellWorkspace.id).where(ProjectCellWorkspace.id == workspace_id)
        )
        is None
    )


# Mutation caught: changing workspace_id ON DELETE from CASCADE.
async def test_deleting_workspace_cascades_operations(
    db_session: AsyncSession,
    project_cell_workspace: ProjectCellWorkspace,
) -> None:
    operation = ProjectCellOperation(
        workspace_id=project_cell_workspace.id,
        kind="destroy",
        status="completed",
        idempotency_key="destroy:workspace",
        request_digest="0" * 64,
    )
    db_session.add(operation)
    await db_session.flush()
    operation_id = operation.id

    await db_session.execute(
        delete(ProjectCellWorkspace).where(
            ProjectCellWorkspace.id == project_cell_workspace.id
        )
    )
    await db_session.flush()

    assert (
        await db_session.scalar(
            select(ProjectCellOperation.id).where(ProjectCellOperation.id == operation_id)
        )
        is None
    )


# Mutation caught: changing either generation_run_id FK away from SET NULL.
async def test_deleting_generation_run_preserves_records_and_clears_links(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> None:
    run = GenerationRun(
        project_id=project.id,
        user_id=user.id,
        idempotency_key="project-cell-generation-run",
        prompt_hash="hash",
    )
    db_session.add(run)
    await db_session.flush()
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=run.id,
    )
    db_session.add(workspace)
    await db_session.flush()
    operation = ProjectCellOperation(
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind="ensure",
        status="completed",
        idempotency_key="ensure:generation-run-delete",
        request_digest="0" * 64,
    )
    db_session.add(operation)
    await db_session.flush()

    await db_session.execute(delete(GenerationRun).where(GenerationRun.id == run.id))
    await db_session.flush()
    await db_session.refresh(workspace)
    await db_session.refresh(operation)

    assert workspace.generation_run_id is None
    assert operation.generation_run_id is None


# Mutation caught: omitting Python/server defaults used by Task 3 reservations.
async def test_workspace_and_operation_defaults_persist(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> None:
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        provider="docker_owner_canary",
        state="provisioning",
    )
    db_session.add(workspace)
    await db_session.flush()
    operation = ProjectCellOperation(
        workspace_id=workspace.id,
        kind="ensure",
        idempotency_key="ensure:defaults",
        request_digest="0" * 64,
    )
    db_session.add(operation)
    await db_session.flush()
    await db_session.refresh(workspace)
    await db_session.refresh(operation)

    assert workspace.provider_metadata == {}
    assert workspace.fencing_epoch == 0
    assert workspace.version == 1
    assert operation.fencing_epoch is None
    assert operation.status == "pending"
    assert operation.request_payload == {}
    assert operation.result_payload is None
