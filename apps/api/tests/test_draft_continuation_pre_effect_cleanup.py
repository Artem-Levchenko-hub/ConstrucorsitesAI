from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.test_project_cell_capacity import _project_run
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.models.user import User
from yleum_api.services import orchestrator_client, project_cell_runtime
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.generation.recovery_requirements import require_quiesced_predecessor
from yleum_api.services.orchestrator_client import (
    ControlProjectCellResourcesRequest,
    EnsureProjectCellResourcesRequest,
    ObserveProjectCellResourcesRequest,
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellLeaseObservation,
    ProjectCellOrchestratorClient,
    ProjectCellResourceResponse,
)
from yleum_api.services.project_cell_capacity import (
    hibernate_one_idle_workspace,
    release_one_stale_generation_lease,
)
from yleum_api.services.project_cell_lifecycle import execute_cell_operation
from yleum_api.services.project_cells import (
    _canonical_operation_envelope,
    _hashed_error,
    execution_run_id,
    reserve_cell_operation,
)


def _control_response(request: ControlProjectCellResourcesRequest) -> ProjectCellResourceResponse:
    return ProjectCellResourceResponse(
        workspace_id=request.workspace_id,
        state="resources_ready" if request.kind == "wake" else "resources_paused",
        provider_ref=None, fencing_epoch=request.fencing_epoch,
        checkpoint_ref=request.checkpoint_ref, has_workspace=True, has_agent_home=True,
        has_postgres=True, has_redis=True,
    )


@pytest_asyncio.fixture
async def rejected_ensure_cleanup(
    test_engine: AsyncEngine,
) -> tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID]:
    return await _run_rejected_ensure_cleanup(test_engine)


async def _run_rejected_ensure_cleanup(
    test_engine: AsyncEngine, *, maintenance: str | None = None,
    reconcile_status: bool = False, reconcile_is_final: bool = False,
) -> tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        owner = User(email=f"pre-effect-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        project, predecessor, workspace = await _project_run(
            session, owner, created_at=now, label="pre-effect",
        )
        _, requester, _ = await _project_run(
            session, owner, created_at=now, label="requester",
        )
        predecessor.status = "running"
        workspace.generation_run_id = predecessor.id
        workspace.fencing_epoch = 5
        ensure, _ = await reserve_cell_operation(
            session, workspace_id=workspace.id, generation_run_id=predecessor.id,
            kind="ensure", idempotency_key=f"pre-effect-test:{predecessor.id}",
            request={"profile_version": "test-v1"},
        )
        await session.commit()

    async def reject(
        request: EnsureProjectCellResourcesRequest | ControlProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse:
        raise OrchestratorBadRequest(
            "confirmed before effect",
            status_code=409,
            details={
                "operation_id": str(request.operation_id),
                "fencing_epoch": request.fencing_epoch,
                "request_digest": request.request_digest,
                "effect_applied": False,
            },
        )

    async def observe(request: ObserveProjectCellResourcesRequest) -> ProjectCellResourceResponse:
        if reconcile_status and request.fencing_epoch == 9:
            raise OrchestratorUnavailable("status response lost")
        epoch = request.fencing_epoch - (2 if reconcile_status else 0)
        paused = maintenance is not None and epoch in {10, 12}
        positive_fences = {12, 14} if maintenance else {9}
        return ProjectCellResourceResponse(
            workspace_id=request.workspace_id, provider_ref=None,
            state="resources_paused" if paused else "resources_ready",
            fencing_epoch=request.fencing_epoch, checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
            lease_observation=ProjectCellLeaseObservation(
                version=1, workspace_id=request.workspace_id, project_id=project.id,
                owner_id=owner.id, operation_id=request.operation_id,
                fencing_epoch=request.fencing_epoch, request_digest=request.request_digest,
                active_generation_run_id=None, active_generation_fencing_epoch=None,
                quiescent=(reconcile_is_final and request.fencing_epoch == 10)
                or (maintenance != "held" and epoch in positive_fences),
            ),
        )

    async def control(request: ControlProjectCellResourcesRequest) -> ProjectCellResourceResponse:
        if request.kind == "release":
            return await reject(request)
        response = _control_response(request)
        if maintenance == "dropped" and request.kind == "restore":
            response = replace(response, state="partial", has_postgres=False)
        return response

    client = cast(ProjectCellOrchestratorClient, SimpleNamespace(
        ensure=reject, control=control, observe_resources=observe,
    ))
    outcome = await execute_cell_operation(factory, ensure.id, client)
    assert outcome.status == "failed" and outcome.fencing_epoch == 6
    async with factory() as session:
        failed = await session.get(ProjectCellOperation, ensure.id)
        run = await session.get(GenerationRun, predecessor.id)
        assert failed is not None and failed.error == _hashed_error("orchestrator_rejected:409")
        assert run is not None
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        await session.commit()

    async def cleanup() -> bool:
        return await release_one_stale_generation_lease(
            factory, requesting_run_id=requester.id, workspace_id=workspace.id,
            expected_generation_run_id=predecessor.id, client=client,
        )

    assert not await cleanup()  # Failed release at fence 7.
    assert not await cleanup()  # Confirmed non-quiescent status at fence 8.
    if reconcile_status:
        assert not await cleanup()  # Status at fence 9 lost its transport response.
        assert await cleanup() is reconcile_is_final  # The worker reconciles it at fence 10.
        if reconcile_is_final:
            return factory, predecessor, workspace.id
    if maintenance:
        checkpoint = f"support-current-{predecessor.id.hex[:16]}"
        for kind, key, request in (
            ("pause", "current-checkpoint", {"checkpoint_ref": checkpoint}),
            ("restore", "restore-current", {"checkpoint_ref": (
                "another-checkpoint" if maintenance == "restore_other" else checkpoint
            )}),
            ("wake", "wake-current", {}),
        ):
            async with factory() as session:
                operation, _ = await reserve_cell_operation(
                    session, workspace_id=workspace.id, generation_run_id=None,
                    kind=kind, idempotency_key=f"support:{key}:{predecessor.id}", request=request,
                )
                await session.commit()
            context = execution_run_id.set(
                requester.id if maintenance == "other_execution" and kind == "restore" else None,
            )
            try:
                outcome = await execute_cell_operation(factory, operation.id, client)
            finally:
                execution_run_id.reset(context)
            assert outcome.status == "completed"
            if kind != "wake":
                assert not await cleanup()  # Historical read-only observations while paused.
    assert await cleanup() is (maintenance != "held")
    return factory, predecessor, workspace.id


async def test_only_draft_opt_in_accepts_quiescent_status_after_rejected_ensure(
    rejected_ensure_cleanup: tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID],
) -> None:
    factory, predecessor, workspace_id = rejected_ensure_cleanup
    async with factory() as session:
        cell = await session.get(ProjectCellWorkspace, workspace_id)
        assert cell is not None and cell.generation_run_id is None and cell.fencing_epoch == 9
        operations = list((await session.scalars(select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace_id,
        ).order_by(ProjectCellOperation.fencing_epoch))).all())
        assert [(op.kind, op.status) for op in operations] == [
            ("ensure", "failed"), ("release", "failed"), ("status", "completed"),
            ("status", "completed"),
        ]
        scope = dict(
            project_id=predecessor.project_id, user_id=predecessor.user_id,
            predecessor_id=predecessor.id,
        )
        with pytest.raises(RecoveryContractError, match="fenced release receipt missing"):
            await require_quiesced_predecessor(session, **scope)
        await require_quiesced_predecessor(
            session, **scope, allow_quiescent_pre_effect_ensure=True,
        )
        reloaded = await session.get(GenerationRun, predecessor.id, populate_existing=True)
        assert reloaded is not None and reloaded.status == "failed"



@pytest.mark.parametrize("maintenance", [
    "same_checkpoint", "restore_other", "dropped", "held", "other_execution",
])
async def test_status_after_completed_support_cycle(
    test_engine: AsyncEngine, maintenance: str,
) -> None:
    factory, predecessor, workspace_id = await _run_rejected_ensure_cleanup(
        test_engine, maintenance=maintenance,
    )
    async with factory() as session:
        cell = await session.get(ProjectCellWorkspace, workspace_id)
        assert cell is not None and cell.fencing_epoch == 14
        assert cell.generation_run_id == (predecessor.id if maintenance == "held" else None)
        scope = dict(
            project_id=predecessor.project_id, user_id=predecessor.user_id,
            predecessor_id=predecessor.id, allow_quiescent_pre_effect_ensure=True,
        )
        if maintenance == "same_checkpoint":
            await require_quiesced_predecessor(session, **scope)
        else:
            with pytest.raises(
                RecoveryContractError,
                match=r"fenced release receipt missing|physical release is unproven",
            ):
                await require_quiesced_predecessor(session, **scope)


async def test_idle_pause_wake_does_not_reuse_detached_predecessor_observation(
    rejected_ensure_cleanup: tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, predecessor, workspace_id = rejected_ensure_cleanup
    async with factory() as session:
        requester = await session.scalar(select(GenerationRun.id).where(
            GenerationRun.user_id == predecessor.user_id, GenerationRun.id != predecessor.id,
        ))
        assert requester is not None

    async def control(request: ControlProjectCellResourcesRequest) -> ProjectCellResourceResponse:
        return _control_response(request)

    async def start_preview(*args, **kwargs):
        return SimpleNamespace(preview_url="https://preview.example.test")

    client = cast(ProjectCellOrchestratorClient, SimpleNamespace(control=control))
    monkeypatch.setattr(orchestrator_client, "HttpProjectCellOrchestratorClient", lambda: client)
    monkeypatch.setattr(orchestrator_client, "project_cell_start_owner_preview", start_preview)
    assert await hibernate_one_idle_workspace(
        factory, requesting_run_id=requester, expected_workspace_id=workspace_id, client=client,
    )
    async with factory() as session:
        cell = await session.get(ProjectCellWorkspace, workspace_id)
        assert cell is not None and cell.state == "stopped" and cell.fencing_epoch == 10
        assert await project_cell_runtime._wake_owner_workspace(
            session, cell, operation=None,
        ) == "https://preview.example.test"

    assert not await release_one_stale_generation_lease(
        factory, requesting_run_id=requester, workspace_id=workspace_id,
        expected_generation_run_id=predecessor.id, client=client,
    )
    async with factory() as session:
        cell = await session.get(ProjectCellWorkspace, workspace_id)
        assert cell is not None and cell.state == "ready" and cell.fencing_epoch == 11
        with pytest.raises(RecoveryContractError, match="fenced release receipt missing"):
            await require_quiesced_predecessor(
                session, project_id=predecessor.project_id, user_id=predecessor.user_id,
                predecessor_id=predecessor.id, allow_quiescent_pre_effect_ensure=True,
            )


@pytest.mark.parametrize("reconcile_is_final", [False, True])
async def test_reconciled_status_requires_separate_current_observation(
    test_engine: AsyncEngine, reconcile_is_final: bool,
) -> None:
    factory, predecessor, workspace_id = await _run_rejected_ensure_cleanup(
        test_engine, maintenance=None if reconcile_is_final else "same_checkpoint",
        reconcile_status=True, reconcile_is_final=reconcile_is_final,
    )
    async with factory() as session:
        cell = await session.get(ProjectCellWorkspace, workspace_id)
        assert cell is not None and cell.generation_run_id is None
        assert cell.fencing_epoch == (10 if reconcile_is_final else 16)
        target, receipt = (await session.scalars(select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace_id,
            ProjectCellOperation.fencing_epoch.in_({9, 10}),
        ).order_by(ProjectCellOperation.fencing_epoch))).all()
        assert target.kind == "status" and target.status == "indeterminate"
        assert receipt.kind == "reconcile" and receipt.status == "completed"
        assert (receipt.result_payload or {}).get("reconciles_operation_id") == str(target.id)
        target_id, receipt_id = target.id, receipt.id
        scope = dict(
            project_id=predecessor.project_id, user_id=predecessor.user_id,
            predecessor_id=predecessor.id, allow_quiescent_pre_effect_ensure=True,
        )
        if reconcile_is_final:
            with pytest.raises(RecoveryContractError, match="fenced release receipt missing"):
                await require_quiesced_predecessor(session, **scope)
            return
        await require_quiesced_predecessor(session, **scope)

    for invalid in ("wrong_reconcile_target", "wrong_lease_operation", "stateful_target"):
        async with factory() as session:
            target = await session.get(ProjectCellOperation, target_id)
            receipt = await session.get(ProjectCellOperation, receipt_id)
            assert target is not None and receipt is not None and receipt.result_payload is not None
            result = dict(receipt.result_payload)
            if invalid == "wrong_reconcile_target":
                result["reconciles_operation_id"] = str(uuid4())
            elif invalid == "wrong_lease_operation":
                proof = dict(cast(dict[str, object], result["lease_observation"]))
                proof["operation_id"] = str(target_id)
                result["lease_observation"] = proof
            else:
                target.kind = "release"
                target.request_payload, target.request_digest = _canonical_operation_envelope(
                    workspace_id, predecessor.id, "release", {},
                )
            receipt.result_payload = result
            await session.flush()
            with pytest.raises(
                RecoveryContractError,
                match=r"fenced release receipt missing|effect remains unknown",
            ):
                await require_quiesced_predecessor(session, **scope)


async def test_status_requires_current_well_bound_positive_observation(
    rejected_ensure_cleanup: tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID],
) -> None:
    factory, predecessor, workspace_id = rejected_ensure_cleanup
    for broken in (
        "missing", "false", "paused", "stale_fence", "history_gap", "logical_holder",
        "wrong_workspace", "wrong_project", "wrong_owner", "wrong_operation", "wrong_request",
        "wrong_source", "wrong_run",
    ):
        async with factory() as session:
            observation = await session.scalar(select(ProjectCellOperation).where(
                ProjectCellOperation.workspace_id == workspace_id,
                ProjectCellOperation.fencing_epoch == 9,
            ))
            assert observation is not None and observation.result_payload is not None
            payload = dict(observation.result_payload)
            proof = dict(cast(dict[str, object], payload["lease_observation"]))
            if broken == "missing":
                payload.pop("lease_observation")
            elif broken == "false":
                proof["quiescent"] = False
            elif broken == "paused":
                payload["state"] = "resources_paused"
            elif broken == "history_gap":
                historical_status = await session.scalar(select(ProjectCellOperation).where(
                    ProjectCellOperation.workspace_id == workspace_id,
                    ProjectCellOperation.fencing_epoch == 8,
                ))
                assert historical_status is not None
                await session.delete(historical_status)
            elif broken in {"stale_fence", "logical_holder"}:
                cell = await session.get(ProjectCellWorkspace, workspace_id)
                assert cell is not None
                if broken == "stale_fence":
                    cell.fencing_epoch += 1
                else:
                    cell.generation_run_id = predecessor.id
            elif broken == "wrong_workspace":
                proof["workspace_id"] = str(uuid4())
            elif broken == "wrong_project":
                proof["project_id"] = str(uuid4())
            elif broken == "wrong_owner":
                proof["owner_id"] = str(uuid4())
            elif broken == "wrong_operation":
                proof["operation_id"] = str(uuid4())
            elif broken == "wrong_request":
                proof["request_digest"] = "b" * 64
            elif broken == "wrong_source":
                observation.idempotency_key = "unrelated-resource-status"
            else:
                other = await session.scalar(select(GenerationRun.id).where(
                    GenerationRun.project_id != predecessor.project_id,
                ))
                assert other is not None
                observation.generation_run_id = other
            if broken != "missing":
                payload["lease_observation"] = proof
            observation.result_payload = payload
            await session.flush()
            with pytest.raises(
                RecoveryContractError,
                match=r"fenced release receipt missing|physical release is unproven",
            ):
                await require_quiesced_predecessor(
                    session, project_id=predecessor.project_id, user_id=predecessor.user_id,
                    predecessor_id=predecessor.id, allow_quiescent_pre_effect_ensure=True,
                )


async def test_status_never_substitutes_for_dispatched_or_unknown_effects(
    rejected_ensure_cleanup: tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID],
) -> None:
    factory, predecessor, workspace_id = rejected_ensure_cleanup
    for history in ("untyped_rejection", "retried_dispatch", "successful_ensure",
                    "unknown", "finished_activity", "active_activity"):
        async with factory() as session:
            ensure = await session.scalar(select(ProjectCellOperation).where(
                ProjectCellOperation.workspace_id == workspace_id,
                ProjectCellOperation.kind == "ensure",
            ))
            assert ensure is not None
            if history == "untyped_rejection":
                ensure.error = _hashed_error("unconfirmed_rejection:409")
            elif history == "retried_dispatch":
                ensure.attempt_count += 1
            elif history == "successful_ensure":
                ensure.status = "completed"
            elif history == "unknown":
                ensure.status = "indeterminate"
            else:
                now = datetime.now(UTC)
                session.add(ProjectCellActivityLease(
                    workspace_id=workspace_id, generation_run_id=predecessor.id,
                    kind="command",
                    state="active" if history == "active_activity" else "completed",
                    fencing_epoch=8, phase="fast_check", started_at=now,
                    heartbeat_at=now, deadline_at=now,
                    finished_at=(None if history == "active_activity" else now),
                ))
            await session.flush()
            with pytest.raises(
                RecoveryContractError,
                match=r"receipt missing|effect remains unknown|activity cleanup is pending",
            ):
                await require_quiesced_predecessor(
                    session, project_id=predecessor.project_id, user_id=predecessor.user_id,
                    predecessor_id=predecessor.id, allow_quiescent_pre_effect_ensure=True,
                )
