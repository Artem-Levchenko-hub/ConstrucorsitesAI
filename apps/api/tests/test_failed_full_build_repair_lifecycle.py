"""Exercise repair routing from an owned persisted failed FULL_BUILD receipt.

The fixture supplies the finished physical command (BEFORE lease / AFTER RED).
The actual repair loop, failed outcome, ORM witness, source_failure and repair
deadline execute unchanged. There is no model, new command or accepted build.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_full_build_failed_repair import FILES, PARTIAL_ASSERTION, response, terminal_status
from tests.test_full_build_failed_repair import witness_case as witness_case
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import ProjectCellActivityLease
from yleum_api.services import max_finalization
from yleum_api.services.generation.recovery_actions import recovery_binding
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.generation.recovery_requirements import load_compiler_witness
from yleum_api.services.project_cell_activity import (
    ActivityKind,
    ActivityStart,
    reconcile_activity,
)
from yleum_api.services.project_cell_executor import ProjectCellCommandRole
from yleum_api.services.project_cell_proofs import ProofDimension


def orm_sessions(case):
    def postgres_dates(value):
        # SQLite drops timezone metadata; native PostgreSQL returns aware dates.
        if isinstance(value, (GenerationRun, ProjectCellActivityLease)):
            for name in ("created_at", "started_at", "finished_at", "deadline_at", "heartbeat_at"):
                if not hasattr(value, name):
                    continue
                date = getattr(value, name)
                if date is not None and date.tzinfo is None:
                    setattr(value, name, date.replace(tzinfo=UTC))
        return value

    class SessionBoundary:
        async def get(self, model, key, **kwargs):
            return postgres_dates(case.sync.get(model, key, **kwargs))

        async def scalar(self, statement):
            return postgres_dates(case.sync.scalar(statement))

        async def commit(self):
            case.sync.commit()

        async def flush(self):
            case.sync.flush()

        def expunge(self, value):
            case.sync.expunge(value)

    @asynccontextmanager
    async def factory():
        yield SessionBoundary()

    return factory


@pytest.mark.parametrize("heartbeat_phase", [None, "final-test", "typecheck"])
async def test_owned_failed_build_reaches_real_witness_and_one_repair_callback(
    witness_case,
    monkeypatch,
    heartbeat_phase,
):
    case = witness_case
    now = datetime.now(UTC)
    case.run.created_at = case.run.started_at = now - timedelta(minutes=1)
    case.run.agent_state = {}
    case.sync.commit()

    factory = orm_sessions(case)

    if heartbeat_phase is not None:
        case.lease.state = "active"
        case.sync.commit()
        running = replace(
            terminal_status(case.response),
            state="running",
            phase=heartbeat_phase,
            terminal_response=None,
        )
        expected = ActivityStart(
            operation_id=case.lease.operation_id,
            workspace_id=case.before.workspace_id,
            generation_run_id=case.run.id,
            kind=ActivityKind.COMMAND,
            fencing_epoch=case.before.fencing_epoch,
            proof_key=case.before.proof_key,
            phase="final_build",
            deadline_at=now + timedelta(minutes=5),
        )
        observed = await reconcile_activity(
            session_factory=factory,
            workspace_id=case.before.workspace_id,
            operation_id=case.lease.operation_id,
            poll_status=AsyncMock(return_value=running),
            expected_lease=expected,
        )
        assert observed.phase == heartbeat_phase
        assert case.lease.phase == "final_build"
        await reconcile_activity(
            session_factory=factory,
            workspace_id=case.before.workspace_id,
            operation_id=case.lease.operation_id,
            poll_status=AsyncMock(return_value=terminal_status(case.response)),
            expected_lease=expected,
        )

    async def replay(role, response, expected):
        assert role is ProjectCellCommandRole.FULL_BUILD
        assert response == case.response and expected == case.before
        return case.observation

    coordinator = object.__new__(max_finalization.MaxFinalizationCoordinator)
    coordinator.project_id, coordinator.generation_run_id = case.project.id, case.run.id
    coordinator.session_factory = factory
    coordinator.continuation = None
    coordinator.emit = AsyncMock()
    coordinator.executor = SimpleNamespace(
        workspace_id=case.after.workspace_id,
        snapshot_files=AsyncMock(return_value=dict(FILES)),
        refresh_snapshot_files=None,
        current_identity=AsyncMock(return_value=case.after),
        operation_status=case.operation_status,
        replay_role_response=replay,
    )

    async def existing_failed_command(*, files, prompt, **options):
        assert files == FILES and prompt == "Build the requested nutrition app"
        return await coordinator._failed(
            case.after,
            case.proof,
            max_finalization.GenerationPhase.FINAL_BUILD,
            case.row,
            case.row.redacted_detail,
            bundle=max_finalization.ProofBundle(identity=case.proof, full_build=case.row),
        )

    # Enter from the already terminal full-build stage, without inventing a
    # second command. Everything after the failed observation is production code.
    coordinator.finalize = existing_failed_command
    loader = max_finalization.load_compiler_witness
    loader_errors = []

    async def observed_loader(*args, **kwargs):
        try:
            return await loader(*args, **kwargs)
        except RecoveryContractError as error:
            loader_errors.append(str(error))
            raise

    monkeypatch.setattr(max_finalization, "load_compiler_witness", observed_loader)
    general = AsyncMock(side_effect=AssertionError("must use the verified source witness"))
    calls = []

    async def source_repair(detail, failure):
        assert "[source-repair:confirmed]" in detail
        assert "[failed-full-build-environment-transition:v1]" in detail
        assert case.lease.proof_key == case.before.proof_key != case.after.proof_key
        if case.response.detail == PARTIAL_ASSERTION:
            assert failure.kind == "test_assertion" and failure.allowed_paths == ()
        calls.append(failure)
        return False  # No source was changed; do not pretend the app is fixed.

    result = await coordinator.finalize_with_repair(
        prompt="Build the requested nutrition app",
        repair=general,
        repair_source=source_repair,
        autonomous_source_repair=True,
        max_source_repairs=1,
    )
    assert result.status is max_finalization.MaxFinalizationStatus.NEEDS_EDIT
    assert len(calls) == 1, {
        "loader_errors": loader_errors,
        "status_reads": case.reads,
        "lease_phase": case.lease.phase,
    }
    general.assert_not_awaited()
    assert case.reads >= 1  # Actual load_compiler_witness read the owned terminal status.
    assert case.lease.proof_key == case.before.proof_key
    assert case.row.outcome == "red" and case.row.artifact_ref is None


@pytest.mark.parametrize(
    "dimension,task_phase",
    [
        (ProofDimension.FULL_BUILD, "final-test"),
        (ProofDimension.FAST_CHECK, "typecheck"),
    ],
)
async def test_historical_task_phase_does_not_grant_new_compiler_authority(
    witness_case,
    dimension,
    task_phase,
):
    case = witness_case
    case.row.dimension = dimension.value
    case.row.dimension_key = case.after.dimension_key(dimension)
    case.lease.phase = task_phase
    if dimension is ProofDimension.FAST_CHECK:
        # Fast-check has no environment transition; bind its original activity
        # to the same physical identity, leaving only the task/role mismatch.
        case.lease.proof_key = case.after.proof_key
    case.sync.commit()
    async with case.factory() as session:
        with pytest.raises(RecoveryContractError):
            await load_compiler_witness(
                session,
                run_id=case.run.id,
                receipt_id=case.row.id,
                binding=recovery_binding(case.after),
            )
    assert case.reads == 0
    assert case.lease.phase == task_phase  # Historical rows are not normalized.
    coordinator = object.__new__(max_finalization.MaxFinalizationCoordinator)
    coordinator.project_id, coordinator.generation_run_id = case.project.id, case.run.id
    coordinator.session_factory = case.factory
    coordinator._identity = AsyncMock(return_value=case.after)
    coordinator._verified_workspace_files = AsyncMock(side_effect=AssertionError("unowned receipt"))
    assert await coordinator.source_failure(case.row) is None
    coordinator._verified_workspace_files.assert_not_awaited()
    assert case.reads == 0


async def test_fast_check_heartbeat_retains_semantic_role_for_real_witness(witness_case):
    case = witness_case
    case.row.dimension = "fast_check"
    case.row.dimension_key = case.after.dimension_key(ProofDimension.FAST_CHECK)
    case.lease.proof_key = case.after.proof_key
    case.lease.phase, case.lease.state = "fast_check", "active"
    case.sync.commit()
    factory = orm_sessions(case)
    terminal = terminal_status(
        response(
            case.after,
            case.after,
            case.lease.operation_id,
            detail=case.response.detail,
        ),
        phase="typecheck",
    )
    expected = ActivityStart(
        operation_id=case.lease.operation_id,
        workspace_id=case.after.workspace_id,
        generation_run_id=case.run.id,
        kind=ActivityKind.COMMAND,
        fencing_epoch=case.after.fencing_epoch,
        proof_key=case.after.proof_key,
        phase="fast_check",
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    for status in (replace(terminal, state="running", terminal_response=None), terminal):
        observed = await reconcile_activity(
            session_factory=factory,
            workspace_id=case.after.workspace_id,
            operation_id=case.lease.operation_id,
            poll_status=AsyncMock(return_value=status),
            expected_lease=expected,
        )
        assert observed.phase == "typecheck" and case.lease.phase == "fast_check"
    async with factory() as session:
        witness = await load_compiler_witness(
            session,
            run_id=case.run.id,
            receipt_id=case.row.id,
            binding=recovery_binding(case.after),
        )
    assert not witness.passed and case.row.artifact_ref is None
