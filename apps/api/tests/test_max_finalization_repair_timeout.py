"""A source-edit cutoff cannot restore files under an unfinished compiler."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_project_cell_activity import _new_project, _new_run, _new_user, _new_workspace
from yleum_api.models.project_cell import ProjectCellActivityLease, ProjectCellProofResult
from yleum_api.services import max_finalization as finalization
from yleum_api.services.generation import agent_finalization
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.max_finalization import (
    MaxFinalizationCoordinator,
    MaxVerificationUnavailable,
)
from yleum_api.services.orchestrator_client import (
    OrchestratorUnavailable,
    ProjectCellAgentExecResponse,
    ProjectCellAgentOperationStatus,
    ProjectCellWorkspaceIdentity,
)
from yleum_api.services.project_cell_activity import (
    ActivityKind,
    ActivityStart,
    run_with_activity_lease,
    start_activity,
)
from yleum_api.services.project_cell_proofs import ProofIdentity


@pytest.fixture
async def repair_case(db_session, test_engine):
    owner = await _new_user(db_session, "repair-timeout")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    identity = ProofIdentity(
        workspace_id=workspace.id, generation_run_id=run.id, fencing_epoch=7,
        workspace_revision="1" * 64, dependency_digest="2" * 64,
        schema_data_digest="3" * 64, cell_manifest_digest="4" * 64,
        base_image_digest="5" * 64, toolchain_digest="5" * 64,
        resource_profile_version="test-v1", build_config_digest="6" * 64,
    )
    now = datetime.now(UTC)
    lease = ActivityStart(
        operation_id=uuid4(), workspace_id=workspace.id, generation_run_id=run.id,
        kind=ActivityKind.COMMAND, fencing_epoch=7, deadline_at=now + timedelta(minutes=5),
        proof_key=identity.proof_key, phase="fast_check",
    )
    await start_activity(
        db_session, operation_id=lease.operation_id, workspace_id=workspace.id,
        generation_run_id=run.id, kind=lease.kind, fencing_epoch=7, now=now,
        deadline_at=lease.deadline_at, proof_key=identity.proof_key, phase=lease.phase,
    )
    await db_session.commit()
    wire_identity = ProjectCellWorkspaceIdentity(
        workspace_revision=identity.workspace_revision,
        dependency_digest=identity.dependency_digest,
        schema_data_digest=identity.schema_data_digest,
        cell_manifest_digest=identity.cell_manifest_digest,
        environment_digest=identity.base_image_digest,
        build_config_digest=identity.build_config_digest,
    )

    def status(*, state="completed", ok=True, operation=None):
        operation = operation or lease.operation_id
        response = None if state == "running" else ProjectCellAgentExecResponse(
            ok=ok, exit_code=0 if ok else 1, detail="green" if ok else "actual compiler error",
            timed_out=False, workspace_revision=identity.workspace_revision, operation_id=operation,
            before_identity=wire_identity, after_identity=wire_identity,
        )
        return ProjectCellAgentOperationStatus(
            operation_id=operation, state=state, phase="fast_check", started_at=now,
            deadline_at=lease.deadline_at, heartbeat_at=datetime.now(UTC), log_bytes=12,
            terminal_response=response,
        )

    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator.session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    coordinator.generation_run_id, coordinator.project_id = run.id, project.id
    coordinator.continuation = None
    coordinator.executor = SimpleNamespace(
        workspace_id=workspace.id, current_identity=AsyncMock(return_value=identity),
        operation_status=AsyncMock(return_value=status()), run_role=AsyncMock(),
    )
    return SimpleNamespace(
        coordinator=coordinator, owner=owner, project=project, run=run, workspace=workspace,
        lease=lease, identity=identity, status=status,
    )


@pytest.mark.parametrize("ok", [True, False])
async def test_timeout_settlement_consumes_exact_terminal_without_new_proof(
    repair_case, db_session, ok,
):
    case = repair_case
    case.coordinator.executor.operation_status.return_value = case.status(ok=ok)
    await case.coordinator._settle_timed_out_source_repair()

    row = await db_session.get(ProjectCellActivityLease, case.lease.operation_id)
    await db_session.refresh(row)
    assert row.state == ("completed" if ok else "failed")
    assert row.redacted_diagnostic == ("green" if ok else "actual compiler error")
    assert await db_session.scalar(select(func.count()).select_from(ProjectCellProofResult)) == 0
    case.coordinator.executor.operation_status.assert_awaited_once_with(case.lease.operation_id)
    case.coordinator.executor.run_role.assert_not_awaited()


@pytest.mark.parametrize("fault", [
    "running", "503", "operation", "fence", "source", "owner", "lease_fence",
    "foreign_run", "workspace_after_poll", "missing_receipt",
])
async def test_unknown_or_foreign_command_never_authorizes_rollback(
    repair_case, db_session, fault,
):
    case, coordinator = repair_case, repair_case.coordinator
    if fault == "running":
        coordinator.executor.operation_status.return_value = case.status(state="running")
    elif fault == "503":
        coordinator.executor.operation_status.side_effect = OrchestratorUnavailable("HTTP 503")
    elif fault == "operation":
        coordinator.executor.operation_status.return_value = case.status(operation=uuid4())
    elif fault in {"fence", "source"}:
        coordinator.executor.current_identity.return_value = replace(
            case.identity, **({"fencing_epoch": 8} if fault == "fence"
                              else {"workspace_revision": "a" * 64}),
        )
    elif fault == "owner":
        foreign = await _new_user(db_session, "foreign-owner")
        case.workspace.owner_id = foreign.id
        await db_session.commit()
    elif fault in {"lease_fence", "foreign_run"}:
        row = await db_session.get(ProjectCellActivityLease, case.lease.operation_id)
        if fault == "lease_fence":
            row.fencing_epoch = 8
        else:
            foreign = await _new_project(db_session, case.owner)
            foreign_run = await _new_run(db_session, case.owner, foreign)
            row.generation_run_id = foreign_run.id
        await db_session.commit()
    elif fault == "workspace_after_poll":
        async def change_fence(_operation):
            case.workspace.fencing_epoch = 8
            await db_session.commit()
            return case.status()

        coordinator.executor.operation_status.side_effect = change_fence
    else:
        coordinator.executor.operation_status.return_value = replace(
            case.status(), terminal_response=None,
        )

    with pytest.raises(MaxVerificationUnavailable):
        await coordinator._settle_timed_out_source_repair()
    row = await db_session.get(ProjectCellActivityLease, case.lease.operation_id)
    await db_session.refresh(row)
    assert row.state == "active" and row.finished_at is None
    coordinator.executor.run_role.assert_not_awaited()


async def test_timeout_without_active_command_keeps_existing_error(
    repair_case, db_session, monkeypatch,
):
    case, coordinator = repair_case, repair_case.coordinator
    row = await db_session.get(ProjectCellActivityLease, case.lease.operation_id)
    row.state, row.finished_at = "completed", datetime.now(UTC)
    await db_session.commit()
    monkeypatch.setattr(finalization, "MIN_SOURCE_REPAIR_SECONDS", 0)
    monkeypatch.setattr(finalization, "source_edit_deadline", lambda deadline, **kwargs: deadline)
    monkeypatch.setattr(finalization, "generation_deadline", lambda run: SimpleNamespace(
        at=datetime.now(UTC) + timedelta(seconds=0.02),
    ))
    coordinator.continuation = SimpleNamespace(
        state=None, soft_edit_deadline=lambda deadline: deadline, soft_boundary=AsyncMock(),
    )

    async def stalled_repair(_detail):
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError, match="deadline exceeded during source repair"):
        await coordinator._invoke_source_repair(
            detail="source error", repair=stalled_repair, repair_source=None, failure=None,
        )
    coordinator.executor.operation_status.assert_not_awaited()
    coordinator.executor.run_role.assert_not_awaited()
    coordinator.continuation.soft_boundary.assert_awaited_once_with(exhausted=True)


@pytest.mark.parametrize("writer_settled", [False, True])
async def test_inner_repair_timeout_settles_the_writer_without_exhausting_the_clock(
    repair_case, monkeypatch, writer_settled,
):
    case, coordinator = repair_case, repair_case.coordinator
    coordinator.continuation = SimpleNamespace(
        state=None, soft_edit_deadline=lambda deadline: deadline, soft_boundary=AsyncMock(),
    )
    monkeypatch.setattr(finalization, "source_edit_deadline", lambda deadline, **kwargs: deadline)
    monkeypatch.setattr(finalization, "generation_deadline", lambda run: SimpleNamespace(
        at=datetime.now(UTC) + timedelta(minutes=15),
    ))
    coordinator.executor.operation_status.return_value = case.status(
        state="completed" if writer_settled else "running",
    )
    primary = TimeoutError("provider_timeout: test provider")
    repair = AsyncMock(side_effect=primary)
    expected = TimeoutError if writer_settled else MaxVerificationUnavailable
    with pytest.raises(expected) as caught:
        await coordinator._invoke_source_repair(
            detail="specific source repair", repair=repair, repair_source=None, failure=None,
        )
    if writer_settled:
        assert caught.value is primary
    else:
        assert caught.value.operation_id == case.lease.operation_id
    repair.assert_awaited_once()
    coordinator.executor.operation_status.assert_awaited_once_with(case.lease.operation_id)
    coordinator.continuation.soft_boundary.assert_not_awaited()
    coordinator.executor.run_role.assert_not_awaited()


@pytest.mark.parametrize("has_product", [False, True])
async def test_source_timeout_keeps_draft_after_three_pending_cancellation_polls(
    repair_case, db_session, monkeypatch, has_product,
):
    case, coordinator = repair_case, repair_case.coordinator
    coordinator.executor.operation_status.return_value = case.status(state="running")
    monkeypatch.setattr(finalization, "MIN_SOURCE_REPAIR_SECONDS", 0)
    monkeypatch.setattr(finalization, "source_edit_deadline", lambda deadline, **kwargs: deadline)
    monkeypatch.setattr(finalization, "generation_deadline", lambda run: SimpleNamespace(
        at=datetime.now(UTC) + timedelta(minutes=5),
    ))
    # Expire the real outer timer once the command owns the writer. A small
    # wall-clock delay can expire in earlier DB work and test a different case.
    repair_window = asyncio.timeout(None)
    first_window = [repair_window]

    def timeout(seconds):
        return first_window.pop() if first_window else asyncio.timeout(seconds)

    monkeypatch.setattr(finalization, "asyncio", SimpleNamespace(timeout=timeout))
    dispatches = 0

    async def controller_work():
        nonlocal dispatches
        dispatches += 1
        repair_window.reschedule(asyncio.get_running_loop().time())
        await asyncio.Event().wait()  # Cancelling this HTTP await does not stop the remote work.

    async def repair(_detail):
        return await run_with_activity_lease(
            session_factory=coordinator.session_factory, lease=case.lease,
            work=controller_work, controller_owned=True,
            poll_status=coordinator.executor.operation_status, emit=AsyncMock(),
        )

    with pytest.raises(MaxVerificationUnavailable) as raised:
        await coordinator._invoke_source_repair(
            detail="specific source repair", repair=repair, repair_source=None, failure=None,
        )
    assert raised.value.operation_id == case.lease.operation_id
    assert dispatches == 1 and coordinator.executor.operation_status.await_count == 4
    row = await db_session.get(ProjectCellActivityLease, case.lease.operation_id)
    await db_session.refresh(row)
    assert row.state == "active" and row.finished_at is None

    # The actual caller preserves the checkpoint on this typed outcome; neither
    # first-build nor accepted-version rollback may write underneath the compiler.
    accepted, candidate = {"src/app/page.tsx": "accepted"}, {"src/app/page.tsx": "draft"}
    restore = AsyncMock()
    core_restore = AsyncMock()
    monkeypatch.setattr(agent_finalization, "_restore_project_cell_source", restore)
    monkeypatch.setattr(agent_finalization, "_restore_max_core", core_restore)
    checkpoint = AsyncMock()
    runtime = GenerationRuntime(
        handle=SimpleNamespace(snapshot_files=AsyncMock(return_value=candidate),
                               prove_restoration_adaptation=None),
        coordinator=SimpleNamespace(finalize_with_repair=AsyncMock(side_effect=raised.value),
                                    session_factory=None),
    )
    with pytest.raises(MaxVerificationUnavailable):
        await agent_finalization.finalize_max_candidate(
            _is_edit=has_product, _max_has_generated_snapshot=has_product, _max_shell_enabled=False,
            accumulated="draft", baseline=SourceBaseline(
                uuid4(), "a" * 40 if has_product else "", accepted if has_product else {},
            ),
            files=candidate, ids=GenerationIds(*(uuid4() for _ in range(5))), is_free=False,
            prompt_text="change design", runtime=runtime, plan=SimpleNamespace(),
            operations=SimpleNamespace(emit=AsyncMock(), checkpoint=checkpoint),
            source_handoff=True,
        )
    checkpoint.assert_awaited_once()
    restore.assert_not_awaited()
    core_restore.assert_not_awaited()

    # The original operation can later be settled by GET; it is never dispatched again.
    coordinator.executor.operation_status.return_value = case.status()
    await coordinator._settle_timed_out_source_repair()
    await db_session.refresh(row)
    assert row.state == "completed" and dispatches == 1
    coordinator.executor.run_role.assert_not_awaited()
