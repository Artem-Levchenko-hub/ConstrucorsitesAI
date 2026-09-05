from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellCapacityRejection,
    ProjectCellCapacityWait,
    ProjectCellResourceResponse,
)
from omnia_api.services.project_cell_capacity import (
    hibernate_one_idle_workspace,
    release_one_stale_generation_lease,
    wait_for_capacity,
)
from omnia_api.services.project_cell_lifecycle import execute_cell_operation
from omnia_api.services.project_cells import (
    claim_cell_operation_committed,
    complete_cell_operation,
    mark_cell_operation_indeterminate,
    reserve_cell_operation,
)

pytestmark = pytest.mark.asyncio


async def _new_owner(session: AsyncSession, label: str) -> User:
    owner = User(email=f"capacity-edge-{label}-{uuid4().hex}@example.test", password_hash="x")
    session.add(owner)
    await session.flush()
    return owner


async def _new_project_run(
    session: AsyncSession,
    owner: User,
    *,
    label: str,
    created_at: datetime,
    status: str = "queued_for_capacity",
) -> tuple[Project, GenerationRun]:
    project = Project(
        owner_id=owner.id,
        name=f"Capacity edge {label}",
        slug=f"capacity-edge-{label}-{uuid4().hex}",
        template="max_miniapp",
    )
    session.add(project)
    await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=f"capacity-edge-{label}-{uuid4().hex}",
        prompt_hash=label * 8,
        status=status,
        created_at=created_at,
    )
    session.add(run)
    await session.flush()
    return project, run


async def _new_workspace(
    session: AsyncSession,
    project: Project,
    owner: User,
    *,
    generation_run_id: UUID | None,
) -> ProjectCellWorkspace:
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=generation_run_id,
        ready_at=datetime.now(UTC),
    )
    session.add(workspace)
    await session.flush()
    return workspace


def _resource_response(
    request: object,
    *,
    state: str = "resources_ready",
) -> ProjectCellResourceResponse:
    return ProjectCellResourceResponse(
        workspace_id=request.workspace_id,  # type: ignore[attr-defined]
        state=state,
        provider_ref=f"cell-{request.workspace_id}",  # type: ignore[attr-defined]
        fencing_epoch=request.fencing_epoch,  # type: ignore[attr-defined]
        checkpoint_ref=getattr(request, "checkpoint_ref", None),
        has_workspace=True,
        has_agent_home=True,
        has_postgres=True,
        has_redis=True,
    )


async def _complete_operation(
    factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    *,
    state: str = "resources_ready",
) -> ProjectCellOperation:
    claimed = await claim_cell_operation_committed(factory, operation_id)
    response = ProjectCellResourceResponse(
        workspace_id=claimed.workspace_id,
        state=state,
        provider_ref=f"cell-{claimed.workspace_id}",
        fencing_epoch=claimed.fencing_epoch,
        checkpoint_ref=None,
        has_workspace=True,
        has_agent_home=True,
        has_postgres=True,
        has_redis=True,
    )
    async with factory() as session:
        await complete_cell_operation(session, operation_id, response.to_wire_json())
        await session.commit()
        operation = await session.get(ProjectCellOperation, operation_id)
        assert operation is not None
        return operation


@pytest.mark.parametrize("with_initial_attempt", [False, True])
async def test_durable_running_ensure_waits_until_bounded_capacity_deadline(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    with_initial_attempt: bool,
) -> None:
    from omnia_api.services import project_cell_capacity

    monkeypatch.setattr(
        project_cell_capacity,
        "get_settings",
        lambda: SimpleNamespace(project_cell_capacity_wait_seconds=0.2),
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_owner(session, f"running-{with_initial_attempt}")
        project, run = await _new_project_run(
            session,
            owner,
            label=f"running-{with_initial_attempt}",
            created_at=datetime.now(UTC),
        )
        workspace = await _new_workspace(
            session,
            project,
            owner,
            generation_run_id=run.id,
        )
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="ensure",
            idempotency_key=f"capacity-edge:running:{run.id}",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()
    await claim_cell_operation_committed(factory, operation.id)

    async def forbidden(_request: object) -> ProjectCellResourceResponse:
        pytest.fail("a durable running operation must not be dispatched again")

    client = SimpleNamespace(ensure=forbidden)

    async def initial_attempt():
        return await execute_cell_operation(factory, operation.id, client)

    with pytest.raises(TimeoutError, match="capacity queue deadline exceeded"):
        await wait_for_capacity(
            factory,
            run_id=run.id,
            operation_id=operation.id,
            client=client,
            emit=lambda _payload: asyncio.sleep(0),
            initial_attempt=initial_attempt if with_initial_attempt else None,
        )

    async with factory() as session:
        persisted = await session.get(ProjectCellOperation, operation.id)
        assert persisted is not None
        assert persisted.status == "running"


async def test_pending_capacity_release_is_adopted_by_next_requester(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_owner(session, "pending-release")
        now = datetime.now(UTC)
        _, first_requester = await _new_project_run(
            session,
            owner,
            label="first-requester",
            created_at=now,
        )
        _, next_requester = await _new_project_run(
            session,
            owner,
            label="next-requester",
            created_at=now + timedelta(seconds=1),
        )
        stale_project, stale_run = await _new_project_run(
            session,
            owner,
            label="stale-release",
            created_at=now - timedelta(minutes=1),
            status="failed",
        )
        workspace = await _new_workspace(
            session,
            stale_project,
            owner,
            generation_run_id=stale_run.id,
        )
        base_key = f"capacity:release:{workspace.id}:{stale_run.id}"
        pending, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=stale_run.id,
            kind="release",
            idempotency_key=f"{base_key}:{first_requester.id.hex[:12]}:1",
            request={},
        )
        await session.commit()

    calls: list[object] = []

    async def control(request: object) -> ProjectCellResourceResponse:
        calls.append(request)
        return _resource_response(request)

    assert await release_one_stale_generation_lease(
        factory,
        requesting_run_id=next_requester.id,
        client=SimpleNamespace(control=control),
    )
    assert len(calls) == 1
    assert calls[0].operation_id == pending.id  # type: ignore[attr-defined]
    async with factory() as session:
        recovered = await session.get(ProjectCellWorkspace, workspace.id)
        assert recovered is not None
        assert recovered.generation_run_id is None
        release_count = await session.scalar(
            select(func.count(ProjectCellOperation.id)).where(
                ProjectCellOperation.workspace_id == workspace.id,
                ProjectCellOperation.kind == "release",
            )
        )
        assert release_count == 1


async def test_pending_capacity_pause_is_adopted_by_next_requester(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_owner(session, "pending-pause")
        now = datetime.now(UTC)
        _, first_requester = await _new_project_run(
            session,
            owner,
            label="first-pause",
            created_at=now,
        )
        _, next_requester = await _new_project_run(
            session,
            owner,
            label="next-pause",
            created_at=now + timedelta(seconds=1),
        )
        victim_project, _ = await _new_project_run(
            session,
            owner,
            label="pause-victim",
            created_at=now - timedelta(minutes=1),
            status="completed",
        )
        victim = await _new_workspace(
            session,
            victim_project,
            owner,
            generation_run_id=None,
        )
        checkpoint_ref = f"capacity-{first_requester.id.hex[:12]}"
        pending, _ = await reserve_cell_operation(
            session,
            workspace_id=victim.id,
            generation_run_id=None,
            kind="pause",
            idempotency_key=f"capacity:{first_requester.id}:pause:{victim.id}",
            request={"checkpoint_ref": checkpoint_ref},
        )
        await session.commit()

    calls: list[object] = []

    async def control(request: object) -> ProjectCellResourceResponse:
        calls.append(request)
        return _resource_response(request, state="resources_paused")

    assert await hibernate_one_idle_workspace(
        factory,
        requesting_run_id=next_requester.id,
        client=SimpleNamespace(control=control),
    )
    assert len(calls) == 1
    assert calls[0].operation_id == pending.id  # type: ignore[attr-defined]
    assert calls[0].checkpoint_ref == checkpoint_ref  # type: ignore[attr-defined]
    async with factory() as session:
        recovered = await session.get(ProjectCellWorkspace, victim.id)
        assert recovered is not None
        assert recovered.state == "stopped"
        pause_count = await session.scalar(
            select(func.count(ProjectCellOperation.id)).where(
                ProjectCellOperation.workspace_id == victim.id,
                ProjectCellOperation.kind == "pause",
            )
        )
        assert pause_count == 1


@pytest.mark.parametrize("later_kind", ["status", "reconcile"])
async def test_completed_release_remains_authoritative_after_completed_observations(
    test_engine: AsyncEngine,
    later_kind: str,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_owner(session, f"release-observation-{later_kind}")
        now = datetime.now(UTC)
        _, requester = await _new_project_run(
            session,
            owner,
            label=f"request-{later_kind}",
            created_at=now,
        )
        stale_project, stale_run = await _new_project_run(
            session,
            owner,
            label=f"stale-{later_kind}",
            created_at=now - timedelta(minutes=1),
            status="failed",
        )
        workspace = await _new_workspace(
            session,
            stale_project,
            owner,
            generation_run_id=stale_run.id,
        )
        release, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=stale_run.id,
            kind="release",
            idempotency_key=f"capacity:release:{workspace.id}:{stale_run.id}",
            request={},
        )
        await session.commit()
    completed_release = await _complete_operation(factory, release.id)

    async with factory() as session:
        observation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=stale_run.id,
            kind=later_kind,
            idempotency_key=f"capacity:later-{later_kind}:{workspace.id}",
            request=(
                {"indeterminate_operation_id": str(uuid4())} if later_kind == "reconcile" else {}
            ),
        )
        await session.commit()
    completed_observation = await _complete_operation(factory, observation.id)
    assert completed_observation.fencing_epoch > completed_release.fencing_epoch

    async def forbidden(_request: object) -> ProjectCellResourceResponse:
        pytest.fail("a completed release must be finalized without another controller effect")

    assert await release_one_stale_generation_lease(
        factory,
        requesting_run_id=requester.id,
        client=SimpleNamespace(control=forbidden),
    )
    async with factory() as session:
        recovered = await session.get(ProjectCellWorkspace, workspace.id)
        assert recovered is not None
        assert recovered.generation_run_id is None


@pytest.mark.parametrize("later_effect", ["unknown_status", "completed_ensure"])
async def test_completed_release_does_not_detach_past_unsettled_or_new_lease_effect(
    test_engine: AsyncEngine,
    later_effect: str,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_owner(session, f"release-veto-{later_effect}")
        now = datetime.now(UTC)
        _, requester = await _new_project_run(
            session,
            owner,
            label=f"request-{later_effect}",
            created_at=now,
        )
        stale_project, stale_run = await _new_project_run(
            session,
            owner,
            label=f"stale-{later_effect}",
            created_at=now - timedelta(minutes=1),
            status="failed",
        )
        workspace = await _new_workspace(
            session,
            stale_project,
            owner,
            generation_run_id=stale_run.id,
        )
        release, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=stale_run.id,
            kind="release",
            idempotency_key=f"capacity:release:{workspace.id}:{stale_run.id}",
            request={},
        )
        await session.commit()
    completed_release = await _complete_operation(factory, release.id)

    async with factory() as session:
        if later_effect == "unknown_status":
            later, _ = await reserve_cell_operation(
                session,
                workspace_id=workspace.id,
                generation_run_id=stale_run.id,
                kind="status",
                idempotency_key=f"capacity:unknown-status:{workspace.id}",
                request={},
            )
        else:
            later_run = GenerationRun(
                project_id=stale_project.id,
                user_id=owner.id,
                idempotency_key=f"capacity-later-ensure-{uuid4().hex}",
                prompt_hash="n" * 64,
                status="failed",
            )
            session.add(later_run)
            await session.flush()
            later, _ = await reserve_cell_operation(
                session,
                workspace_id=workspace.id,
                generation_run_id=later_run.id,
                kind="ensure",
                idempotency_key=f"capacity:later-ensure:{workspace.id}",
                request={"profile_version": "docker-owner-cell-resources-v1"},
            )
        await session.commit()
    if later_effect == "unknown_status":
        await claim_cell_operation_committed(factory, later.id)
        async with factory() as session:
            await mark_cell_operation_indeterminate(session, later.id, "unknown")
            await session.commit()
    else:
        await _complete_operation(factory, later.id)
    assert (await _read_fence(factory, later.id)) > completed_release.fencing_epoch

    async def unavailable(_request: object) -> ProjectCellResourceResponse:
        raise OrchestratorUnavailable("controller effect unavailable")

    assert not await release_one_stale_generation_lease(
        factory,
        requesting_run_id=requester.id,
        client=SimpleNamespace(control=unavailable, observe_resources=unavailable),
    )
    async with factory() as session:
        retained = await session.get(ProjectCellWorkspace, workspace.id)
        assert retained is not None
        assert retained.generation_run_id == stale_run.id


async def _read_fence(
    factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
) -> int:
    async with factory() as session:
        operation = await session.get(ProjectCellOperation, operation_id)
        assert operation is not None
        assert operation.fencing_epoch is not None
        return operation.fencing_epoch


async def test_release_cooldown_is_shared_across_requesters(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_owner(session, "shared-cooldown")
        now = datetime.now(UTC)
        _, first_requester = await _new_project_run(
            session,
            owner,
            label="cooldown-first",
            created_at=now,
        )
        _, next_requester = await _new_project_run(
            session,
            owner,
            label="cooldown-next",
            created_at=now + timedelta(seconds=1),
        )
        failed_project, failed_run = await _new_project_run(
            session,
            owner,
            label="cooldown-failed",
            created_at=now - timedelta(minutes=2),
            status="failed",
        )
        other_project, other_run = await _new_project_run(
            session,
            owner,
            label="cooldown-other",
            created_at=now - timedelta(minutes=1),
            status="completed",
        )
        failed_cell = await _new_workspace(
            session,
            failed_project,
            owner,
            generation_run_id=failed_run.id,
        )
        other_cell = await _new_workspace(
            session,
            other_project,
            owner,
            generation_run_id=other_run.id,
        )
        failed_cell.ready_at = now - timedelta(minutes=2)
        other_cell.ready_at = now - timedelta(minutes=1)
        await session.commit()

    calls: list[object] = []

    async def control(request: object) -> ProjectCellResourceResponse:
        calls.append(request)
        if request.workspace_id == failed_cell.id:  # type: ignore[attr-defined]
            raise OrchestratorBadRequest(
                "confirmed rejection",
                status_code=409,
                details={
                    "operation_id": str(request.operation_id),  # type: ignore[attr-defined]
                    "fencing_epoch": request.fencing_epoch,  # type: ignore[attr-defined]
                    "request_digest": request.request_digest,  # type: ignore[attr-defined]
                    "effect_applied": False,
                },
            )
        return _resource_response(request)

    assert not await release_one_stale_generation_lease(
        factory,
        requesting_run_id=first_requester.id,
        client=SimpleNamespace(control=control),
    )
    assert await release_one_stale_generation_lease(
        factory,
        requesting_run_id=next_requester.id,
        client=SimpleNamespace(control=control),
    )
    assert [request.workspace_id for request in calls] == [  # type: ignore[attr-defined]
        failed_cell.id,
        other_cell.id,
    ]
    async with factory() as session:
        failed = await session.get(ProjectCellWorkspace, failed_cell.id)
        recovered = await session.get(ProjectCellWorkspace, other_cell.id)
        assert failed is not None and failed.generation_run_id == failed_run.id
        assert recovered is not None and recovered.generation_run_id is None


async def test_zero_effect_repair_capacity_wait_cools_old_victim_and_reclaims_next(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        owner = await _new_owner(session, "zero-effect-cooldown")
        _, requester = await _new_project_run(
            session,
            owner,
            label="zero-effect-requester",
            created_at=now,
        )
        older_project, older_run = await _new_project_run(
            session,
            owner,
            label="zero-effect-older",
            created_at=now - timedelta(minutes=2),
            status="failed",
        )
        newer_project, newer_run = await _new_project_run(
            session,
            owner,
            label="zero-effect-newer",
            created_at=now - timedelta(minutes=1),
            status="completed",
        )
        older = await _new_workspace(
            session,
            older_project,
            owner,
            generation_run_id=older_run.id,
        )
        older.state = "provisioning"
        older.ready_at = now - timedelta(minutes=2)
        newer = await _new_workspace(
            session,
            newer_project,
            owner,
            generation_run_id=newer_run.id,
        )
        newer.ready_at = now - timedelta(minutes=1)
        unknown_ensure, _ = await reserve_cell_operation(
            session,
            workspace_id=older.id,
            generation_run_id=older_run.id,
            kind="ensure",
            idempotency_key=f"capacity-edge:unknown-zero:{older_run.id}",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()
    await claim_cell_operation_committed(factory, unknown_ensure.id)
    async with factory() as session:
        await mark_cell_operation_indeterminate(
            session,
            unknown_ensure.id,
            "ensure response unknown",
        )
        await session.commit()

    observed: list[UUID] = []
    ensured: list[UUID] = []
    released: list[UUID] = []

    async def observe(request: object) -> ProjectCellResourceResponse:
        observed.append(request.workspace_id)  # type: ignore[attr-defined]
        return ProjectCellResourceResponse(
            workspace_id=request.workspace_id,  # type: ignore[attr-defined]
            state="partial",
            provider_ref=f"cell-{request.workspace_id}",  # type: ignore[attr-defined]
            fencing_epoch=request.fencing_epoch,  # type: ignore[attr-defined]
            checkpoint_ref=None,
            has_workspace=False,
            has_agent_home=False,
            has_postgres=False,
            has_redis=False,
        )

    async def ensure(request: object) -> ProjectCellResourceResponse:
        ensured.append(request.workspace_id)  # type: ignore[attr-defined]
        raise ProjectCellCapacityWait(
            ProjectCellCapacityRejection(
                operation_id=request.operation_id,  # type: ignore[attr-defined]
                fencing_epoch=request.fencing_epoch,  # type: ignore[attr-defined]
                request_digest=request.request_digest,  # type: ignore[attr-defined]
                effect_applied=False,
                reason="insufficient_memory",
                retry_after_seconds=2,
            )
        )

    async def control(request: object) -> ProjectCellResourceResponse:
        released.append(request.workspace_id)  # type: ignore[attr-defined]
        return _resource_response(request)

    client = SimpleNamespace(
        observe_resources=observe,
        ensure=ensure,
        control=control,
    )
    assert not await release_one_stale_generation_lease(
        factory,
        requesting_run_id=requester.id,
        client=client,
    )
    async with factory() as session:
        deferred = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == older.id,
                ProjectCellOperation.idempotency_key.startswith("cell-recovery:repair:"),
            )
            .order_by(ProjectCellOperation.created_at.desc())
            .limit(1)
        )
        assert deferred is not None
        assert deferred.status == "cancelled"
        assert deferred.next_attempt_at is not None
        assert deferred.next_attempt_at > datetime.now(UTC) + timedelta(seconds=20)

    assert await release_one_stale_generation_lease(
        factory,
        requesting_run_id=requester.id,
        client=client,
    )
    assert observed == [older.id]
    assert ensured == [older.id]
    assert released == [newer.id]
    async with factory() as session:
        retained = await session.get(ProjectCellWorkspace, older.id)
        reclaimed = await session.get(ProjectCellWorkspace, newer.id)
        assert retained is not None and retained.generation_run_id == older_run.id
        assert reclaimed is not None and reclaimed.generation_run_id is None
