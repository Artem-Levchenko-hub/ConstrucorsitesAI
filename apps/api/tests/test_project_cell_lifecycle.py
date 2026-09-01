from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellResourceResponse,
)
from omnia_api.services.project_cell_lifecycle import (
    execute_cell_operation,
    reconcile_indeterminate_cell_operation,
)
from omnia_api.services.project_cells import (
    claim_cell_operation_committed,
    reserve_cell_operation,
)

pytestmark = pytest.mark.asyncio

_LIFECYCLE_NAMES = (
    "execute_cell_operation",
    "reconcile_indeterminate_cell_operation",
)


class CommitFailed(RuntimeError):
    pass


@dataclass(slots=True)
class CommitController:
    fail_on_calls: set[int] = field(default_factory=set)
    cancel_on_calls: set[int] = field(default_factory=set)
    commit_calls: int = 0


class ControlledAsyncSession(AsyncSession):
    async def commit(self) -> None:
        controller = self.info.get("commit_controller")
        if isinstance(controller, CommitController):
            controller.commit_calls += 1
            if controller.commit_calls in controller.cancel_on_calls:
                controller.cancel_on_calls.remove(controller.commit_calls)
                await super().rollback()
                raise asyncio.CancelledError()
            if controller.commit_calls in controller.fail_on_calls:
                controller.fail_on_calls.remove(controller.commit_calls)
                await super().rollback()
                raise CommitFailed(f"forced commit failure #{controller.commit_calls}")
        await super().commit()


@dataclass(slots=True)
class ClientHarness:
    ensure: AsyncMock
    control: AsyncMock
    observe_resources: AsyncMock

    @classmethod
    def with_default_response(
        cls,
        workspace_id: UUID,
        *,
        fence: int,
        state: str = "resources_ready",
        checkpoint_ref: str | None = None,
    ) -> ClientHarness:
        response = ProjectCellResourceResponse(
            workspace_id=workspace_id,
            state=state,
            provider_ref="cell-1",
            fencing_epoch=fence,
            checkpoint_ref=checkpoint_ref,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )
        return cls(
            ensure=AsyncMock(return_value=response),
            control=AsyncMock(return_value=response),
            observe_resources=AsyncMock(return_value=response),
        )


async def _new_user(session: AsyncSession, label: str) -> User:
    user = User(email=f"cell-life-{label}-{uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _new_project(session: AsyncSession, owner: User, label: str = "project") -> Project:
    project = Project(
        owner_id=owner.id,
        name=f"Project Cell {label}",
        slug=f"cell-life-{label}-{uuid4().hex}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    return project


async def _new_workspace(
    session: AsyncSession,
    project: Project,
    owner: User,
) -> ProjectCellWorkspace:
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="ready",
    )
    session.add(workspace)
    await session.flush()
    return workspace


def _factory(
    engine: AsyncEngine,
    controller: CommitController | None = None,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=ControlledAsyncSession,
        expire_on_commit=False,
        info={"commit_controller": controller},
    )


async def _reserve_operation(
    factory: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    request: dict[str, object],
    idempotency_key: str,
) -> tuple[ProjectCellWorkspace, ProjectCellOperation]:
    async with factory() as session:
        owner = await _new_user(session, idempotency_key.replace(":", "-"))
        project = await _new_project(session, owner, idempotency_key.replace(":", "-"))
        workspace = await _new_workspace(session, project, owner)
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind=kind,
            idempotency_key=idempotency_key,
            request=request,
        )
        await session.commit()
        return workspace, operation


async def _read_operation(
    factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
) -> ProjectCellOperation:
    async with factory() as session:
        operation = await session.get(ProjectCellOperation, operation_id)
        assert operation is not None
        return operation


async def test_zero_outbound_call_before_claim_commit(
    test_engine: AsyncEngine,
) -> None:
    controller = CommitController(fail_on_calls={1})
    setup_factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        setup_factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:claim-commit-fails",
    )
    factory = _factory(test_engine, controller)
    client = ClientHarness.with_default_response(workspace.id, fence=1)

    with pytest.raises(CommitFailed):
        await execute_cell_operation(factory, operation.id, client)

    assert client.ensure.await_count == 0
    assert client.control.await_count == 0
    assert client.observe_resources.await_count == 0
    stored = await _read_operation(factory, operation.id)
    assert stored.status == "pending"
    assert stored.fencing_epoch is None


async def test_terminal_commit_failure_becomes_indeterminate(
    test_engine: AsyncEngine,
) -> None:
    controller = CommitController(fail_on_calls={2})
    setup_factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        setup_factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:terminal-commit-fails",
    )
    factory = _factory(test_engine, controller)
    client = ClientHarness.with_default_response(workspace.id, fence=1)

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "indeterminate"
    assert outcome.response is None
    assert client.ensure.await_count == 1
    stored = await _read_operation(factory, operation.id)
    assert stored.status == "indeterminate"
    assert stored.fencing_epoch == 1

    async with factory() as session:
        reconcile, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="reconcile",
            idempotency_key="reconcile:higher-fence",
            request={"indeterminate_operation_id": str(operation.id)},
        )
        await session.commit()

    claimed_reconcile = await claim_cell_operation_committed(factory, reconcile.id)
    assert claimed_reconcile.fencing_epoch > stored.fencing_epoch


async def test_cancellation_during_terminal_commit_becomes_indeterminate(
    test_engine: AsyncEngine,
) -> None:
    controller = CommitController(cancel_on_calls={2})
    setup_factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        setup_factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:terminal-commit-cancelled",
    )
    factory = _factory(test_engine, controller)
    client = ClientHarness.with_default_response(workspace.id, fence=1)

    with pytest.raises(asyncio.CancelledError):
        await execute_cell_operation(factory, operation.id, client)

    assert client.ensure.await_count == 1
    stored = await _read_operation(factory, operation.id)
    assert stored.status == "indeterminate"
    assert stored.fencing_epoch == 1


async def test_timeout_is_indeterminate_and_never_replayed(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:timeout",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)
    client.ensure.side_effect = httpx.ReadTimeout("unknown")

    outcome = await execute_cell_operation(factory, operation.id, client)
    replay = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "indeterminate"
    assert replay.status == "indeterminate"
    assert client.ensure.await_count == 1


async def test_cancelled_after_dispatch_is_indeterminate(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:cancelled",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)
    client.ensure.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await execute_cell_operation(factory, operation.id, client)

    assert client.ensure.await_count == 1
    stored = await _read_operation(factory, operation.id)
    assert stored.status == "indeterminate"


async def test_database_digest_reaches_exact_outbound_body_unchanged(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:digest",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)

    outcome = await execute_cell_operation(factory, operation.id, client)
    sent = client.ensure.await_args.args[0]
    stored = await _read_operation(factory, operation.id)

    assert outcome.status == "completed"
    assert sent.operation_id == operation.id
    assert sent.fencing_epoch == 1
    assert sent.request_digest == stored.request_digest
    assert outcome.response is not None
    assert outcome.response.fencing_epoch == 1


async def test_confirmed_4xx_rejection_is_failed(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:rejected",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)
    client.ensure.side_effect = OrchestratorBadRequest(
        "bad request",
        status_code=409,
        details={
            "operation_id": str(operation.id),
            "fencing_epoch": 1,
            "request_digest": (await _read_operation(factory, operation.id)).request_digest,
            "effect_applied": False,
        },
    )

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert client.ensure.await_count == 1


@pytest.mark.parametrize(
    "details",
    [
        None,
        {
            "operation_id": "00000000-0000-0000-0000-000000000000",
            "fencing_epoch": 1,
            "request_digest": "a" * 64,
            "effect_applied": False,
        },
        {
            "operation_id": str(uuid4()),
            "fencing_epoch": 999,
            "request_digest": "b" * 64,
            "effect_applied": False,
        },
        {
            "operation_id": str(uuid4()),
            "fencing_epoch": 1,
            "request_digest": "c" * 64,
            "effect_applied": True,
        },
        {"detail": "plain 409"},
    ],
)
async def test_unconfirmed_4xx_rejection_is_indeterminate(
    test_engine: AsyncEngine,
    details: dict[str, object] | None,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key=f"ensure:unconfirmed-{uuid4().hex}",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)
    client.ensure.side_effect = OrchestratorBadRequest(
        "bad request",
        status_code=409,
        details=details,
    )

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "indeterminate"
    assert client.ensure.await_count == 1


async def test_local_request_validation_fails_before_outbound_call(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="ensure",
        request={"profile_version": "v1", "extra": "dropped-field"},
        idempotency_key="ensure:invalid-local-shape",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "failed"
    assert client.ensure.await_count == 0
    assert client.control.await_count == 0
    assert client.observe_resources.await_count == 0


async def test_invalid_checkpoint_ref_fails_before_outbound_call(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="pause",
        request={"checkpoint_ref": "../escape"},
        idempotency_key="pause:invalid-checkpoint",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "failed"
    assert client.ensure.await_count == 0
    assert client.control.await_count == 0
    assert client.observe_resources.await_count == 0


async def test_status_operation_uses_observe_resources(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="status",
        request={},
        idempotency_key="status:observe",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "completed"
    assert client.ensure.await_count == 0
    assert client.control.await_count == 0
    assert client.observe_resources.await_count == 1


async def test_malformed_success_becomes_indeterminate(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="status",
        request={},
        idempotency_key="status:malformed-success",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=1)
    client.observe_resources.return_value = {
        "workspace_id": str(workspace.id),
        "state": "resources_ready",
    }

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "indeterminate"
    assert client.observe_resources.await_count == 1


async def test_response_workspace_mismatch_becomes_indeterminate(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    _workspace, operation = await _reserve_operation(
        factory,
        kind="status",
        request={},
        idempotency_key="status:workspace-mismatch",
    )
    client = ClientHarness.with_default_response(uuid4(), fence=1)

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "indeterminate"
    assert client.observe_resources.await_count == 1


async def test_response_fence_mismatch_becomes_indeterminate(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="status",
        request={},
        idempotency_key="status:fence-mismatch",
    )
    client = ClientHarness.with_default_response(workspace.id, fence=999)

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "indeterminate"
    assert client.observe_resources.await_count == 1


async def test_response_checkpoint_mismatch_becomes_indeterminate(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="pause",
        request={"checkpoint_ref": "accepted-1"},
        idempotency_key="pause:checkpoint-mismatch",
    )
    client = ClientHarness.with_default_response(
        workspace.id,
        fence=1,
        checkpoint_ref="different-checkpoint",
    )

    outcome = await execute_cell_operation(factory, operation.id, client)

    assert outcome.status == "indeterminate"
    assert client.control.await_count == 1


async def test_reconcile_observes_only_and_records_target_operation_id(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, operation = await _reserve_operation(
        factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:unknown",
    )
    unknown_client = ClientHarness.with_default_response(workspace.id, fence=1)
    unknown_client.ensure.side_effect = OrchestratorUnavailable("network lost")
    unknown = await execute_cell_operation(factory, operation.id, unknown_client)
    assert unknown.status == "indeterminate"

    async with factory() as session:
        reconcile, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="reconcile",
            idempotency_key="reconcile:observe-only",
            request={"indeterminate_operation_id": str(operation.id)},
        )
        await session.commit()

    client = ClientHarness.with_default_response(workspace.id, fence=2, state="resources_paused")
    outcome = await reconcile_indeterminate_cell_operation(
        factory,
        operation.id,
        reconcile.id,
        client,
    )

    assert outcome.status == "completed"
    assert outcome.reconciles_operation_id == operation.id
    assert outcome.response is not None
    assert outcome.response.state == "resources_paused"
    assert client.ensure.await_count == 0
    assert client.control.await_count == 0
    assert client.observe_resources.await_count == 1


async def test_reconcile_cannot_switch_durable_target(
    test_engine: AsyncEngine,
) -> None:
    factory = _factory(test_engine)
    workspace, first = await _reserve_operation(
        factory,
        kind="ensure",
        request={"profile_version": "docker-owner-cell-resources-v1"},
        idempotency_key="ensure:unknown-first",
    )
    first_client = ClientHarness.with_default_response(workspace.id, fence=1)
    first_client.ensure.side_effect = OrchestratorUnavailable("network lost")
    assert (await execute_cell_operation(factory, first.id, first_client)).status == (
        "indeterminate"
    )

    async with factory() as session:
        second, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="status",
            idempotency_key="status:unknown-second",
            request={},
        )
        await session.commit()
    second_client = ClientHarness.with_default_response(workspace.id, fence=2)
    second_client.observe_resources.side_effect = OrchestratorUnavailable("network lost")
    assert (await execute_cell_operation(factory, second.id, second_client)).status == (
        "indeterminate"
    )

    async with factory() as session:
        reconcile, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="reconcile",
            idempotency_key="reconcile:bound-first",
            request={"indeterminate_operation_id": str(first.id)},
        )
        await session.commit()

    client = ClientHarness.with_default_response(workspace.id, fence=3)
    outcome = await reconcile_indeterminate_cell_operation(
        factory,
        second.id,
        reconcile.id,
        client,
    )

    assert outcome.status == "failed"
    assert client.observe_resources.await_count == 0


@pytest.mark.filterwarnings(
    "ignore:The test <Function test_public_code_has_no_lifecycle_caller> is marked "
    "with '@pytest.mark.asyncio':pytest.PytestWarning"
)
def test_public_code_has_no_lifecycle_caller() -> None:
    repo = Path(__file__).resolve().parents[1]
    for relative_path in (
        "src/omnia_api/routers/messages.py",
        "src/omnia_api/services/agent_native.py",
        "src/omnia_api/services/agent_builder.py",
    ):
        content = (repo / relative_path).read_text(encoding="utf-8")
        for name in _LIFECYCLE_NAMES:
            assert name not in content
