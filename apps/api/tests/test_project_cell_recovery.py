from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import yleum_api.services.project_cell_recovery as recovery_service
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.user import User
from yleum_api.services.orchestrator_client import (
    EnsureProjectCellResourcesRequest,
    OrchestratorUnavailable,
    ProjectCellCapacityRejection,
    ProjectCellCapacityWait,
    ProjectCellResourceResponse,
)
from yleum_api.services.project_cell_recovery import recover_ensure_operation
from yleum_api.services.project_cells import (
    ProjectCellBusy,
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


async def test_ready_observation_must_acquire_generation_lease_before_admission(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(factory, label="ready-needs-lease")
    client = ClientHarness(
        ensure=AsyncMock(return_value=_response(workspace.id, fence=3, state="resources_ready")),
        control=AsyncMock(),
        observe_resources=AsyncMock(
            return_value=_response(workspace.id, fence=2, state="resources_ready")
        ),
    )

    outcome = await recover_ensure_operation(factory, original.id, client)

    assert outcome.kind == "ensure", "resource observation is not generation lease authority"
    assert outcome.status == "completed"
    assert outcome.response is not None and outcome.response.fencing_epoch == 3
    client.ensure.assert_awaited_once()
    request = client.ensure.await_args.args[0]
    assert request.generation_run_id == run.id
    assert request.fencing_epoch == 3
    operations = await _operations(factory, workspace.id)
    assert [item.kind for item in operations] == ["ensure", "reconcile", "ensure"]
    assert operations[0].status == "indeterminate"


async def test_ready_reconcile_requires_higher_fence_ensure(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(factory, label="ready")
    client = ClientHarness(
        ensure=AsyncMock(return_value=_response(workspace.id, fence=3, state="resources_ready")),
        control=AsyncMock(),
        observe_resources=AsyncMock(
            return_value=_response(workspace.id, fence=2, state="resources_ready")
        ),
    )

    outcome = await recover_ensure_operation(factory, original.id, client)

    operations = await _operations(factory, workspace.id)
    assert outcome.status == "completed"
    assert outcome.kind == "ensure"
    assert outcome.response is not None
    assert outcome.response.state == "resources_ready"
    assert outcome.response.fencing_epoch == 3
    assert operations[0].status == "indeterminate"
    assert operations[1].kind == "reconcile"
    assert operations[1].generation_run_id == run.id
    assert _target_id(operations[1]) == original.id
    assert client.observe_resources.await_count == 1
    assert client.ensure.await_count == 1


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
        ensure=AsyncMock(return_value=_response(workspace.id, fence=4, state="resources_ready")),
        control=AsyncMock(),
        observe_resources=AsyncMock(
            return_value=_response(workspace.id, fence=3, state="resources_ready")
        ),
    )
    outcome = await recover_ensure_operation(factory, original.id, ready_client)

    operations = await _operations(factory, workspace.id)
    reconciliations = [item for item in operations if item.kind == "reconcile"]
    assert outcome.status == "completed"
    assert outcome.response is not None and outcome.response.fencing_epoch == 4
    assert len(reconciliations) == 2
    assert reconciliations[0].status == "indeterminate"
    assert _target_id(reconciliations[0]) == original.id
    assert _target_id(reconciliations[1]) == reconciliations[0].id
    assert operations[0].status == "indeterminate"


@pytest.mark.parametrize("observed_state", ["partial", "retained", "resources_paused"])
async def test_partial_observation_runs_canonical_repair_ensure(
    test_engine: AsyncEngine,
    observed_state: str,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(factory, label="partial")
    client = ClientHarness(
        ensure=AsyncMock(return_value=_response(workspace.id, fence=3, state="resources_ready")),
        control=AsyncMock(),
        observe_resources=AsyncMock(
            return_value=_response(workspace.id, fence=2, state=observed_state),
        ),
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
        ensure=AsyncMock(return_value=_response(workspace.id, fence=5, state="resources_ready")),
        control=AsyncMock(),
        observe_resources=AsyncMock(
            return_value=_response(workspace.id, fence=4, state="resources_ready")
        ),
    )
    second = await recover_ensure_operation(factory, original.id, second_client)

    operations = await _operations(factory, workspace.id)
    repair = next(item for item in operations if item.kind == "ensure" and item.id != original.id)
    latest_reconcile = operations[-2]
    assert second.status == "completed"
    assert second.response is not None and second.response.fencing_epoch == 5
    assert latest_reconcile.kind == "reconcile"
    assert _target_id(latest_reconcile) == repair.id
    assert second_client.observe_resources.await_count == 1
    assert second_client.ensure.await_count == 1
    assert second_client.ensure.await_args.args[0].operation_id not in {original.id, repair.id}


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


@pytest.mark.parametrize(
    "alter",
    [
        None,
        "owner",
        "project",
        "operation",
        "fence",
        "active",
        "unknown",
        "missing",
        "unknown_then_ready",
        "missing_then_ready",
    ],
)
async def test_terminal_resource_only_reconcile_clears_binding_without_fake_lease(
    test_engine: AsyncEngine,
    alter: str | None,
) -> None:
    from yleum_api.services.project_cell_capacity import release_one_stale_generation_lease

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(factory, label="terminal-no-lease")
    async with factory() as session:
        saved = await session.get(GenerationRun, run.id)
        assert saved is not None
        saved.status = "failed"
        saved.finished_at = datetime.now(UTC)
        await session.commit()

    observations = 0

    async def observe(request):
        nonlocal observations
        observations += 1
        payload = _response(
            workspace.id, fence=request.fencing_epoch, state="resources_ready"
        ).to_wire_json()
        payload["lease_observation"] = {
            "version": 1,
            "workspace_id": str(workspace.id),
            "project_id": str(workspace.project_id),
            "owner_id": str(workspace.owner_id),
            "operation_id": str(request.operation_id),
            "request_digest": request.request_digest,
            "fencing_epoch": request.fencing_epoch,
            "active_generation_run_id": None,
            "active_generation_fencing_epoch": None,
            "quiescent": True,
        }
        proof = payload["lease_observation"]
        if alter in {"owner", "project", "operation"}:
            proof[f"{alter}_id"] = str(uuid4())
        elif alter == "fence":
            proof["fencing_epoch"] += 1
        elif alter == "active":
            proof.update(
                active_generation_run_id=str(uuid4()),
                active_generation_fencing_epoch=1,
                quiescent=False,
            )
        elif alter == "unknown" or (alter == "unknown_then_ready" and observations < 3):
            proof["quiescent"] = False
        elif alter == "missing" or (alter == "missing_then_ready" and observations < 3):
            payload.pop("lease_observation")
        return ProjectCellResourceResponse.from_json(payload)

    client = ClientHarness(
        ensure=AsyncMock(), control=AsyncMock(), observe_resources=AsyncMock(side_effect=observe)
    )
    if alter == "active":
        with pytest.raises(ProjectCellStateConflict, match="belongs to another run"):
            await release_one_stale_generation_lease(
                factory,
                requesting_run_id=run.id,
                workspace_id=workspace.id,
                client=client,
            )
        client.ensure.assert_not_awaited()
        client.control.assert_not_awaited()
        return
    released = await release_one_stale_generation_lease(
        factory,
        requesting_run_id=run.id,
        workspace_id=workspace.id,
        expected_generation_run_id=run.id,
        client=client,
    )
    if alter in {"unknown_then_ready", "missing_then_ready"}:
        assert not released
        assert not await release_one_stale_generation_lease(
            factory,
            requesting_run_id=run.id,
            workspace_id=workspace.id,
            client=client,
        )
        released = await release_one_stale_generation_lease(
            factory,
            requesting_run_id=run.id,
            workspace_id=workspace.id,
            client=client,
        )
        assert observations == 3, (
            "completed negative status must obtain a fresh durable observation"
        )
        alter = None
    if alter is not None:
        assert not released
        async with factory() as session:
            assert (
                await session.get(ProjectCellWorkspace, workspace.id)
            ).generation_run_id == run.id
        client.ensure.assert_not_awaited()
        client.control.assert_not_awaited()
        return
    assert released, "terminal idle observation must settle SQL lease without fake ensure/release"
    async with factory() as session:
        saved_workspace = await session.get(ProjectCellWorkspace, workspace.id)
        assert saved_workspace is not None and saved_workspace.generation_run_id is None
        saved_original = await session.get(ProjectCellOperation, original.id)
        assert saved_original is not None and saved_original.status == "indeterminate"
        fence = saved_workspace.fencing_epoch
    assert not await release_one_stale_generation_lease(
        factory,
        requesting_run_id=run.id,
        workspace_id=workspace.id,
        client=client,
    )
    async with factory() as session:
        assert (await session.get(ProjectCellWorkspace, workspace.id)).fencing_epoch == fence
    client.ensure.assert_not_awaited()
    client.control.assert_not_awaited()


async def test_repair_does_not_lock_workspace_before_release_owner_run(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services.project_cells import complete_cell_operation

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(factory, label="lock-order")
    async with factory() as session:
        observation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="reconcile",
            idempotency_key="test:lock-order-observe",
            request={"indeterminate_operation_id": str(original.id)},
        )
        await session.commit()
    claimed = await claim_cell_operation_committed(factory, observation.id)
    async with factory() as session:
        await complete_cell_operation(
            session,
            observation.id,
            _response(
                workspace.id,
                fence=claimed.fencing_epoch,
                state="resources_ready",
            ).to_wire_json(),
        )
        await session.commit()

    entered = asyncio.Event()
    real_lock = recovery_service._lock_and_validate_tail

    async def track_lock(*args, **kwargs):
        entered.set()
        return await real_lock(*args, **kwargs)

    monkeypatch.setattr(recovery_service, "_lock_and_validate_tail", track_lock)
    repair = None
    try:
        async with factory() as release:
            # The actual executor release contract owns Project, Run, then Workspace.
            await release.get(Project, workspace.project_id, with_for_update=True)
            await release.get(GenerationRun, run.id, with_for_update=True)
            repair = asyncio.create_task(
                recovery_service._reserve_repair_ensure(
                    factory,
                    recovery_service._RecoveryContext(workspace.id, run.id),
                    expected_tail_id=observation.id,
                    observation_id=observation.id,
                )
            )
            # On the old implementation this confirms Workspace is reached before Run.
            try:
                await asyncio.wait_for(entered.wait(), 0.2)
            except TimeoutError:
                pass
            await asyncio.sleep(0.05)
            await release.execute(text("SET LOCAL lock_timeout = '500ms'"))
            await release.get(ProjectCellWorkspace, workspace.id, with_for_update=True)
            await release.rollback()
        decision = await asyncio.wait_for(repair, 3)
        assert decision.should_dispatch
    finally:
        if repair is not None and not repair.done():
            repair.cancel()
            await asyncio.gather(repair, return_exceptions=True)


async def test_actual_release_and_repair_serialize_before_owner_and_workspace_locks(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services import project_cell_executor as executor
    from yleum_api.services.project_cells import complete_cell_operation

    basic = async_sessionmaker(test_engine, expire_on_commit=False)
    workspace, run, original = await _unknown_ensure(basic, label="release-repair")
    async with basic() as session:
        observation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="reconcile",
            idempotency_key="test:release-repair-observe",
            request={"indeterminate_operation_id": str(original.id)},
        )
        await session.commit()
    claim = await claim_cell_operation_committed(basic, observation.id)
    async with basic() as session:
        await complete_cell_operation(
            session,
            observation.id,
            _response(
                workspace.id,
                fence=claim.fencing_epoch,
                state="resources_ready",
            ).to_wire_json(),
        )
        await session.commit()

    owner_locked, proceed = asyncio.Event(), asyncio.Event()

    class CoordinatedSession(AsyncSession):
        async def get(self, entity, ident, **kwargs):
            value = await super().get(entity, ident, **kwargs)
            task = asyncio.current_task()
            if (
                entity is GenerationRun
                and kwargs.get("with_for_update")
                and task is not None
                and task.get_name() == "own-release"
                and not owner_locked.is_set()
            ):
                owner_locked.set()
                await proceed.wait()
            return value

    factory = async_sessionmaker(test_engine, class_=CoordinatedSession, expire_on_commit=False)

    async def control(request):
        return _response(workspace.id, fence=request.fencing_epoch, state="resources_ready")

    client = ClientHarness(
        ensure=AsyncMock(), observe_resources=AsyncMock(), control=AsyncMock(side_effect=control)
    )
    monkeypatch.setattr(executor, "HttpProjectCellOrchestratorClient", lambda: client)
    release = asyncio.create_task(
        executor._release_generation_lease(
            session_factory=factory,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            profile_version=_PROFILE,
        ),
        name="own-release",
    )
    repair = None
    try:
        await asyncio.wait_for(owner_locked.wait(), 2)
        repair = asyncio.create_task(
            recovery_service._reserve_repair_ensure(
                factory,
                recovery_service._RecoveryContext(workspace.id, run.id),
                expected_tail_id=observation.id,
                observation_id=observation.id,
            )
        )
        await asyncio.sleep(0.1)
        proceed.set()
        results = await asyncio.wait_for(asyncio.gather(release, repair, return_exceptions=True), 5)
        assert results[0] is None
        assert not isinstance(results[1], BaseException) or isinstance(
            results[1], (ProjectCellStateConflict, ProjectCellBusy)
        )
        client.control.assert_awaited_once()
        client.ensure.assert_not_awaited()
        async with basic() as session:
            assert (await session.get(ProjectCellWorkspace, workspace.id)).generation_run_id is None
    finally:
        proceed.set()
        for task in (release, repair):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *[task for task in (release, repair) if task is not None], return_exceptions=True,
        )
