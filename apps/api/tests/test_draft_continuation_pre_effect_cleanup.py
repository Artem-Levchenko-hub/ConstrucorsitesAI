from __future__ import annotations

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
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
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
from yleum_api.services.project_cells import reserve_cell_operation


def _control_response(request: ControlProjectCellResourcesRequest) -> ProjectCellResourceResponse:
    return ProjectCellResourceResponse(
        workspace_id=request.workspace_id,
        state="resources_ready" if request.kind == "wake" else "resources_paused",
        provider_ref=None, fencing_epoch=request.fencing_epoch,
        checkpoint_ref=request.checkpoint_ref, has_workspace=True, has_agent_home=True,
        has_postgres=True, has_redis=True,
    )


async def _recover_predecessor(
    engine: AsyncEngine, *, reconcile_is_current: bool = False,
) -> tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        owner = User(email=f"continuation-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        project, predecessor, workspace = await _project_run(
            session, owner, created_at=now, label="predecessor",
        )
        _, requester, _ = await _project_run(
            session, owner, created_at=now, label="requester",
        )
        predecessor.status = "running"
        workspace.generation_run_id = predecessor.id
        ensure, _ = await reserve_cell_operation(
            session, workspace_id=workspace.id, generation_run_id=predecessor.id,
            kind="ensure", idempotency_key="original-ensure",
            request={"profile_version": "test-v1"},
        )
        await session.commit()

    async def rejected(
        request: EnsureProjectCellResourcesRequest | ControlProjectCellResourcesRequest,
    ) -> ProjectCellResourceResponse:
        raise OrchestratorBadRequest(
            "confirmed before effect", status_code=409,
            details={
                "operation_id": str(request.operation_id),
                "fencing_epoch": request.fencing_epoch,
                "request_digest": request.request_digest,
                "effect_applied": False,
            },
        )

    async def control(request: ControlProjectCellResourcesRequest) -> ProjectCellResourceResponse:
        if request.kind == "release":
            return await rejected(request)
        return _control_response(request)

    async def observe(request: ObserveProjectCellResourcesRequest) -> ProjectCellResourceResponse:
        epoch = request.fencing_epoch
        if epoch == 3:
            raise OrchestratorUnavailable("observation response lost")
        return ProjectCellResourceResponse(
            workspace_id=request.workspace_id, provider_ref=None,
            state="resources_paused" if epoch == 6 else "resources_ready",
            fencing_epoch=epoch, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
            has_postgres=True, has_redis=True,
            lease_observation=ProjectCellLeaseObservation(
                version=1, workspace_id=request.workspace_id, project_id=project.id,
                owner_id=owner.id, operation_id=request.operation_id,
                fencing_epoch=epoch, request_digest=request.request_digest,
                active_generation_run_id=None, active_generation_fencing_epoch=None,
                quiescent=epoch in ({4} if reconcile_is_current else {6, 9}),
            ),
        )

    client = cast(ProjectCellOrchestratorClient, SimpleNamespace(
        ensure=rejected, control=control, observe_resources=observe,
    ))
    first = await execute_cell_operation(factory, ensure.id, client)
    assert first.status == "failed" and first.fencing_epoch == 1
    async with factory() as session:
        run = await session.get(GenerationRun, predecessor.id)
        assert run is not None
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        await session.commit()

    async def cleanup() -> bool:
        return await release_one_stale_generation_lease(
            factory, requesting_run_id=requester.id, workspace_id=workspace.id,
            expected_generation_run_id=predecessor.id, client=client,
        )

    assert not await cleanup()  # Release rejected.
    assert not await cleanup()  # Status response lost.
    assert await cleanup() is reconcile_is_current  # Canonical reconciliation.
    if not reconcile_is_current:
        for kind in ("pause", "restore", "wake"):
            async with factory() as session:
                operation, _ = await reserve_cell_operation(
                    session, workspace_id=workspace.id, generation_run_id=None,
                    kind=kind, idempotency_key=f"maintenance:{kind}",
                    request={"checkpoint_ref": "owned-current-snapshot"} if kind != "wake" else {},
                )
                await session.commit()
            outcome = await execute_cell_operation(factory, operation.id, client)
            assert outcome.status == "completed"
            if kind == "pause":
                assert not await cleanup()  # Positive physical observation while still paused.
        assert await cleanup()
    return factory, predecessor, workspace.id


@pytest_asyncio.fixture
async def recovered_predecessor(
    test_engine: AsyncEngine,
) -> tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID]:
    return await _recover_predecessor(test_engine)


@pytest.mark.parametrize("kind", ["status", "reconcile"])
async def test_only_draft_accepts_the_capacity_workers_current_receipt(
    test_engine: AsyncEngine, kind: str,
) -> None:
    factory, predecessor, workspace_id = await _recover_predecessor(
        test_engine, reconcile_is_current=kind == "reconcile",
    )
    async with factory() as session:
        workspace = await session.get(ProjectCellWorkspace, workspace_id)
        assert workspace is not None and workspace.generation_run_id is None
        receipt = await session.scalar(select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace_id,
            ProjectCellOperation.fencing_epoch == workspace.fencing_epoch,
        ))
        assert receipt is not None and receipt.kind == kind and receipt.status == "completed"
        scope = dict(project_id=predecessor.project_id, user_id=predecessor.user_id,
                     predecessor_id=predecessor.id)
        with pytest.raises(RecoveryContractError, match="fenced release receipt missing"):
            await require_quiesced_predecessor(session, **scope)
        await require_quiesced_predecessor(session, **scope, allow_current_lease_observation=True)
        run = await session.get(GenerationRun, predecessor.id)
        assert run is not None and run.status == "failed"


async def test_current_receipt_does_not_admit_false_foreign_or_unresolved_state(
    recovered_predecessor: tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID],
) -> None:
    factory, predecessor, workspace_id = recovered_predecessor
    for invalid in ("false", "owner", "operation", "digest", "holder", "running", "unresolved"):
        async with factory() as session:
            workspace = await session.get(ProjectCellWorkspace, workspace_id)
            assert workspace is not None
            receipt = await session.scalar(select(ProjectCellOperation).where(
                ProjectCellOperation.workspace_id == workspace_id,
                ProjectCellOperation.fencing_epoch == workspace.fencing_epoch,
            ))
            assert receipt is not None and receipt.result_payload is not None
            if invalid == "holder":
                workspace.generation_run_id = predecessor.id
            elif invalid in {"running", "unresolved"}:
                lifecycle = await session.scalar(select(ProjectCellOperation).where(
                    ProjectCellOperation.workspace_id == workspace_id,
                    ProjectCellOperation.kind == "restore",
                ))
                assert lifecycle is not None
                lifecycle.status = "running" if invalid == "running" else "indeterminate"
            else:
                unchanged = receipt.result_payload
                proof = dict(cast(dict[str, object], unchanged["lease_observation"]))
                key, value = {
                    "false": ("quiescent", False), "owner": ("owner_id", str(uuid4())),
                    "operation": ("operation_id", str(uuid4())),
                    "digest": ("request_digest", "b" * 64),
                }[invalid]
                proof[key] = value
                receipt.result_payload = unchanged | {"lease_observation": proof}
            await session.flush()
            with pytest.raises(RecoveryContractError):
                await require_quiesced_predecessor(
                    session, project_id=predecessor.project_id, user_id=predecessor.user_id,
                    predecessor_id=predecessor.id, allow_current_lease_observation=True,
                )


async def test_idle_pause_wake_does_not_reuse_a_stale_receipt(
    recovered_predecessor: tuple[async_sessionmaker[AsyncSession], GenerationRun, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, predecessor, workspace_id = recovered_predecessor
    async with factory() as session:
        requester = await session.scalar(select(GenerationRun.id).where(
            GenerationRun.user_id == predecessor.user_id, GenerationRun.id != predecessor.id,
        ))
        assert requester is not None
        workspace = await session.get(ProjectCellWorkspace, workspace_id)
        assert workspace is not None
        previous_fence = workspace.fencing_epoch

    async def control(request: ControlProjectCellResourcesRequest) -> ProjectCellResourceResponse:
        return _control_response(request)

    async def start_preview(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(preview_url="https://preview.example.test")

    client = cast(ProjectCellOrchestratorClient, SimpleNamespace(control=control))
    monkeypatch.setattr(orchestrator_client, "HttpProjectCellOrchestratorClient", lambda: client)
    monkeypatch.setattr(orchestrator_client, "project_cell_start_owner_preview", start_preview)
    assert await hibernate_one_idle_workspace(
        factory, requesting_run_id=requester, expected_workspace_id=workspace_id, client=client,
    )
    async with factory() as session:
        cell = await session.get(ProjectCellWorkspace, workspace_id)
        assert cell is not None and cell.state == "stopped"
        assert await project_cell_runtime._wake_owner_workspace(
            session, cell, operation=None,
        ) == "https://preview.example.test"

    assert not await release_one_stale_generation_lease(
        factory, requesting_run_id=requester, workspace_id=workspace_id,
        expected_generation_run_id=predecessor.id, client=client,
    )
    async with factory() as session:
        cell = await session.get(ProjectCellWorkspace, workspace_id)
        assert cell is not None and cell.state == "ready"
        assert cell.fencing_epoch == previous_fence + 2
        with pytest.raises(RecoveryContractError, match="fenced release receipt missing"):
            await require_quiesced_predecessor(
                session, project_id=predecessor.project_id, user_id=predecessor.user_id,
                predecessor_id=predecessor.id, allow_current_lease_observation=True,
            )
