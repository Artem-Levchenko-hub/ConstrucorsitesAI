from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import omnia_api.services.project_cell_recovery as recovery_service
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.orchestrator_client import (
    EnsureProjectCellResourcesRequest,
    OrchestratorUnavailable,
    ProjectCellCapacityRejection,
    ProjectCellCapacityWait,
    ProjectCellResourceResponse,
)
from omnia_api.services.project_cell_recovery import recover_ensure_operation
from omnia_api.services.project_cells import (
    ProjectCellStateConflict,
    _advisory_lock,
    claim_cell_operation_committed,
    mark_cell_operation_indeterminate,
    reserve_cell_operation,
)

pytestmark = pytest.mark.asyncio

_PROFILE = "docker-owner-cell-resources-v1"


@dataclass(slots=True)
class ClientHarness:
    ensure: AsyncMock
    control: AsyncMock
    observe_resources: AsyncMock
    agent_operation_status: AsyncMock = field(default_factory=AsyncMock)


def _response(
    workspace_id: UUID,
    *,
    fence: int,
    state: str,
) -> ProjectCellResourceResponse:
    ready = state == "resources_ready"
    return ProjectCellResourceResponse(
        workspace_id=workspace_id,
        state=state,
        provider_ref="cell-recovery-test",
        fencing_epoch=fence,
        checkpoint_ref=None,
        has_workspace=ready,
        has_agent_home=ready,
        has_postgres=ready,
        has_redis=ready,
    )


async def _unknown_ensure(
    factory: async_sessionmaker[AsyncSession],
    *,
    label: str,
) -> tuple[ProjectCellWorkspace, GenerationRun, ProjectCellOperation]:
    async with factory() as session:
        owner = User(email=f"recovery-{label}-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        project = Project(
            owner_id=owner.id,
            name=f"Recovery {label}",
            slug=f"recovery-{label}-{uuid4().hex}",
            template="blank",
        )
        session.add(project)
        await session.flush()
        run = GenerationRun(
            project_id=project.id,
            user_id=owner.id,
            idempotency_key=f"recovery-{label}-{uuid4().hex}",
            prompt_hash="a" * 64,
            status="running",
        )
        session.add(run)
        await session.flush()
        workspace = ProjectCellWorkspace(
            project_id=project.id,
            owner_id=owner.id,
            provider="docker_owner_canary",
            state="provisioning",
            generation_run_id=run.id,
            provider_metadata={"profile_version": _PROFILE},
        )
        session.add(workspace)
        await session.flush()
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="ensure",
            idempotency_key=f"generation:{run.id}:ensure:{_PROFILE}",
            request={"profile_version": _PROFILE},
        )
        await session.commit()
    await claim_cell_operation_committed(factory, operation.id)
    async with factory() as session:
        await mark_cell_operation_indeterminate(session, operation.id, "response unknown")
        await session.commit()
    return workspace, run, operation


async def _operations(
    factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
) -> list[ProjectCellOperation]:
    async with factory() as session:
        return list(
            await session.scalars(
                select(ProjectCellOperation)
                .where(ProjectCellOperation.workspace_id == workspace_id)
                .order_by(
                    ProjectCellOperation.fencing_epoch.asc().nullsfirst(),
                    ProjectCellOperation.created_at,
                )
            )
        )


def _target_id(operation: ProjectCellOperation) -> UUID:
    envelope = cast(dict[str, object], operation.request_payload)
    request = cast(dict[str, object], envelope["request"])
    return UUID(cast(str, request["indeterminate_operation_id"]))


async def test_ready_reconcile_returns_higher_fence_proof(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(factory, label="ready")
    client = ClientHarness(
        ensure=AsyncMock(),
        control=AsyncMock(),
        observe_resources=AsyncMock(
            return_value=_response(workspace.id, fence=2, state="resources_ready")
        ),
    )

    outcome = await recover_ensure_operation(factory, original.id, client)

    operations = await _operations(factory, workspace.id)
    assert outcome.status == "completed"
    assert outcome.kind == "reconcile"
    assert outcome.response is not None
    assert outcome.response.state == "resources_ready"
    assert outcome.response.fencing_epoch == 2
    assert operations[0].status == "indeterminate"
    assert operations[1].kind == "reconcile"
    assert operations[1].generation_run_id == run.id
    assert _target_id(operations[1]) == original.id
    assert client.observe_resources.await_count == 1
    assert client.ensure.await_count == 0


async def test_cancelled_reconcile_is_reconciled_at_a_higher_fence(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, _, original = await _unknown_ensure(factory, label="cancel-chain")
    cancelled_client = ClientHarness(
        ensure=AsyncMock(),
        control=AsyncMock(),
        observe_resources=AsyncMock(side_effect=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await recover_ensure_operation(factory, original.id, cancelled_client)

    ready_client = ClientHarness(
        ensure=AsyncMock(),
        control=AsyncMock(),
        observe_resources=AsyncMock(
            return_value=_response(workspace.id, fence=3, state="resources_ready")
        ),
    )
    outcome = await recover_ensure_operation(factory, original.id, ready_client)

    operations = await _operations(factory, workspace.id)
    reconciliations = [item for item in operations if item.kind == "reconcile"]
    assert outcome.status == "completed"
    assert outcome.response is not None and outcome.response.fencing_epoch == 3
    assert len(reconciliations) == 2
    assert reconciliations[0].status == "indeterminate"
    assert _target_id(reconciliations[0]) == original.id
    assert _target_id(reconciliations[1]) == reconciliations[0].id
    assert operations[0].status == "indeterminate"


async def test_partial_observation_runs_canonical_repair_ensure(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(factory, label="partial")
    client = ClientHarness(
        ensure=AsyncMock(return_value=_response(workspace.id, fence=3, state="resources_ready")),
        control=AsyncMock(),
        observe_resources=AsyncMock(return_value=_response(workspace.id, fence=2, state="partial")),
    )

    outcome = await recover_ensure_operation(factory, original.id, client)

    operations = await _operations(factory, workspace.id)
    repair = operations[-1]
    repair_envelope = cast(dict[str, object], repair.request_payload)
    repair_request = cast(dict[str, object], repair_envelope["request"])
    assert outcome.status == "completed"
    assert outcome.kind == "ensure"
    assert outcome.response is not None and outcome.response.fencing_epoch == 3
    assert repair.kind == "ensure"
    assert repair.generation_run_id == run.id
    assert repair_request == {"profile_version": _PROFILE}
    assert repair.idempotency_key.startswith("cell-recovery:repair:")
    assert client.observe_resources.await_count == 1
    assert client.ensure.await_count == 1


async def test_restart_from_original_id_uses_latest_unknown_repair(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, _, original = await _unknown_ensure(factory, label="restart")
    first_client = ClientHarness(
        ensure=AsyncMock(side_effect=OrchestratorUnavailable("repair response lost")),
        control=AsyncMock(),
        observe_resources=AsyncMock(return_value=_response(workspace.id, fence=2, state="partial")),
    )
    first = await recover_ensure_operation(factory, original.id, first_client)
    assert first.status == "indeterminate"
    assert first.kind == "ensure"

    second_client = ClientHarness(
        ensure=AsyncMock(),
        control=AsyncMock(),
        observe_resources=AsyncMock(
            return_value=_response(workspace.id, fence=4, state="resources_ready")
        ),
    )
    second = await recover_ensure_operation(factory, original.id, second_client)

    operations = await _operations(factory, workspace.id)
    repair = next(item for item in operations if item.kind == "ensure" and item.id != original.id)
    latest_reconcile = operations[-1]
    assert second.status == "completed"
    assert second.response is not None and second.response.fencing_epoch == 4
    assert latest_reconcile.kind == "reconcile"
    assert _target_id(latest_reconcile) == repair.id
    assert second_client.observe_resources.await_count == 1
    assert second_client.ensure.await_count == 0


async def test_repair_does_not_dispatch_after_workspace_generation_rebind(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(factory, label="rebind-race")
    repair_cutpoint = asyncio.Event()
    continue_repair = asyncio.Event()
    original_reserve_repair = recovery_service._reserve_repair_ensure

    async def delayed_reserve_repair(
        delayed_factory: async_sessionmaker[AsyncSession],
        context: recovery_service._RecoveryContext,
        *,
        expected_tail_id: UUID,
        observation_id: UUID,
    ) -> recovery_service._ReservationDecision:
        repair_cutpoint.set()
        await continue_repair.wait()
        return await original_reserve_repair(
            delayed_factory,
            context,
            expected_tail_id=expected_tail_id,
            observation_id=observation_id,
        )

    monkeypatch.setattr(
        recovery_service,
        "_reserve_repair_ensure",
        delayed_reserve_repair,
    )
    client = ClientHarness(
        ensure=AsyncMock(return_value=_response(workspace.id, fence=3, state="resources_ready")),
        control=AsyncMock(),
        observe_resources=AsyncMock(return_value=_response(workspace.id, fence=2, state="partial")),
    )
    recovery = asyncio.create_task(recover_ensure_operation(factory, original.id, client))
    await repair_cutpoint.wait()

    async with factory() as session:
        original_run = await session.get(GenerationRun, run.id)
        assert original_run is not None
        original_run.status = "failed"
        await session.flush()
        replacement = GenerationRun(
            project_id=run.project_id,
            user_id=run.user_id,
            idempotency_key=f"recovery-replacement-{uuid4().hex}",
            prompt_hash="b" * 64,
            status="running",
        )
        session.add(replacement)
        await session.flush()
        await _advisory_lock(session, workspace.id)
        locked_workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == workspace.id)
            .with_for_update()
        )
        assert locked_workspace is not None
        locked_workspace.generation_run_id = replacement.id
        await session.commit()

    continue_repair.set()
    with pytest.raises(
        ProjectCellStateConflict,
        match="generation lease changed during recovery",
    ):
        await recovery

    operations = await _operations(factory, workspace.id)
    assert not any(
        operation.idempotency_key.startswith("cell-recovery:repair:") for operation in operations
    )
    assert client.observe_resources.await_count == 1
    assert client.ensure.await_count == 0


async def test_cancelled_repair_retries_from_completed_partial_observation(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, _, original = await _unknown_ensure(factory, label="cancelled-repair")

    async def wait_for_capacity(
        request: EnsureProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse:
        raise ProjectCellCapacityWait(
            ProjectCellCapacityRejection(
                operation_id=request.operation_id,
                fencing_epoch=request.fencing_epoch,
                request_digest=request.request_digest,
                effect_applied=False,
                reason="insufficient_memory",
                retry_after_seconds=1,
            )
        )

    first_client = ClientHarness(
        ensure=AsyncMock(side_effect=wait_for_capacity),
        control=AsyncMock(),
        observe_resources=AsyncMock(return_value=_response(workspace.id, fence=2, state="partial")),
    )
    first = await recover_ensure_operation(factory, original.id, first_client)
    assert first.status == "waiting_capacity"
    assert first.kind == "ensure"
    async with factory() as session:
        repair = await session.get(ProjectCellOperation, first.operation_id)
        assert repair is not None
        repair.status = "cancelled"
        repair.finished_at = datetime.now(UTC)
        repair.next_attempt_at = None
        repair.capacity_reason = None
        await session.commit()

    second_client = ClientHarness(
        ensure=AsyncMock(return_value=_response(workspace.id, fence=4, state="resources_ready")),
        control=AsyncMock(),
        observe_resources=AsyncMock(),
    )
    second = await recover_ensure_operation(factory, original.id, second_client)

    operations = await _operations(factory, workspace.id)
    repairs = [
        operation
        for operation in operations
        if operation.idempotency_key.startswith("cell-recovery:repair:")
    ]
    assert second.status == "completed"
    assert second.kind == "ensure"
    assert second.response is not None and second.response.fencing_epoch == 4
    assert [repair.status for repair in repairs] == ["cancelled", "completed"]
    assert second_client.ensure.await_count == 1
    assert second_client.observe_resources.await_count == 0
