from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellCandidate,
    ProjectCellProofResult,
)
from omnia_api.services.max_finalization import MaxFinalizationStatus
from omnia_api.services.project_cell_executor import ProjectCellCommandRole
from tests.test_max_finalization import _files, _new_harness


async def test_finalization_returns_missing_capability_to_same_workspace_editor(
    db_session, test_engine,
):
    harness = await _new_harness(db_session, test_engine)
    tree = _files()
    feedback = []

    async def snapshot():
        return dict(tree)

    async def repair(detail):
        feedback.append(detail)
        tree["src/app/page.tsx"] = (
            "export default function Page(){return <main>Каталог товаров</main>}"
        )

    harness.coordinator.executor = replace(harness.coordinator.executor, snapshot_files=snapshot)
    outcome = await harness.coordinator.finalize_with_repair(prompt="Каталог", repair=repair)

    assert outcome.status is MaxFinalizationStatus.COMPLETE
    assert len(feedback) == 1 and "каталог" in feedback[0]
    assert harness.roles.count(ProjectCellCommandRole.FULL_BUILD) == 1


@pytest.mark.parametrize("changes, expected_calls", [(False, 1), (True, 2)])
async def test_finalization_repairs_stop_without_success_or_unbounded_retries(
    db_session, test_engine, changes, expected_calls,
):
    harness = await _new_harness(db_session, test_engine)
    tree = _files()
    calls = []

    async def snapshot():
        return dict(tree)

    async def repair(detail):
        calls.append(detail)
        if changes:
            tree["src/app/page.tsx"] += "\n// still incomplete"

    harness.coordinator.executor = replace(harness.coordinator.executor, snapshot_files=snapshot)
    outcome = await harness.coordinator.finalize_with_repair(prompt="Каталог", repair=repair)

    assert outcome.status is MaxFinalizationStatus.NEEDS_EDIT
    assert len(calls) == expected_calls
    assert harness.roles == []


async def test_finalization_does_not_hide_editor_provider_failure(db_session, test_engine):
    harness = await _new_harness(db_session, test_engine)

    async def repair(detail):
        raise RuntimeError("PROVIDER_AUTH_FAILED")

    with pytest.raises(RuntimeError, match="PROVIDER_AUTH_FAILED"):
        await harness.coordinator.finalize_with_repair(prompt="Каталог", repair=repair)
    assert harness.roles == []


async def test_identity_mutation_keeps_compiler_diagnostics(db_session, test_engine):
    harness = await _new_harness(db_session, test_engine)
    original = harness.coordinator.executor.run_role

    async def run_role(role, operation_id):
        result = await original(role, operation_id)
        if role is ProjectCellCommandRole.FAST_CHECK:
            return replace(
                result, ok=False,
                after=replace(result.after, workspace_revision="a" * 64),
                redacted_detail="src/Stock.tsx(12,3): TS2322: label is not an Input prop",
            )
        return result

    harness.coordinator.executor = replace(harness.coordinator.executor, run_role=run_role)
    result = await harness.coordinator.fast_check()
    assert result.outcome == "red"
    assert "frozen proof identity" in result.redacted_detail
    assert "src/Stock.tsx(12,3): TS2322" in result.redacted_detail


async def test_authored_max_finalization_is_single_pass_and_terminal(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _new_harness(db_session, test_engine)

    fast_check = await harness.coordinator.fast_check()
    outcome = await harness.coordinator.finalize(
        files=_files(),
        prompt="Build tracker",
    )

    assert fast_check.outcome == "green"
    assert outcome.status is MaxFinalizationStatus.COMPLETE
    assert harness.roles == [
        ProjectCellCommandRole.BOOTSTRAP,
        ProjectCellCommandRole.FAST_CHECK,
        ProjectCellCommandRole.FULL_BUILD,
    ]
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        dimensions = list(
            await session.scalars(
                select(ProjectCellProofResult.dimension).order_by(
                    ProjectCellProofResult.dimension
                )
            )
        )
        candidate_count = int(
            await session.scalar(select(func.count(ProjectCellCandidate.id))) or 0
        )
        accepted_count = int(
            await session.scalar(
                select(func.count(ProjectCellCandidate.id)).where(
                    ProjectCellCandidate.status == "accepted"
                )
            )
            or 0
        )
        active_leases = int(
            await session.scalar(
                select(func.count(ProjectCellActivityLease.operation_id)).where(
                    ProjectCellActivityLease.state == "active"
                )
            )
            or 0
        )

    assert dimensions == [
        "bootstrap",
        "fast_check",
        "full_build",
        "release",
        "runtime",
    ]
    assert candidate_count == accepted_count == 1
    assert active_leases == 0


@pytest.mark.parametrize("change_source", [False, True])
async def test_missing_production_build_repairs_test_in_same_workspace(
    db_session, test_engine, change_source,
):
    harness = await _new_harness(db_session, test_engine)
    executor = harness.coordinator.executor
    identity = await executor.current_identity()
    tree = _files()
    feedback = []
    original_role = executor.run_role

    async def snapshot():
        return dict(tree)

    async def current_identity():
        return identity

    async def run_role(role, operation_id):
        result = await original_role(role, operation_id)
        result = replace(result, before=identity, after=identity)
        if role is ProjectCellCommandRole.FULL_BUILD and not feedback:
            return replace(result, ok=False, redacted_detail=(
                "service web readiness failed: Could not find a production build "
                "in the '.next' directory. Tests ran next dev after next build."
                + "\n[successful task log] " + "x" * 8000
            ))
        return result

    async def repair(detail):
        nonlocal identity
        feedback.append(detail)
        if change_source:
            tree["tests/runtime.test.mjs"] = "// test production server in an isolated port"
            identity = replace(identity, workspace_revision="a" * 64)

    harness.coordinator.executor = replace(
        executor, snapshot_files=snapshot, current_identity=current_identity, run_role=run_role,
    )
    outcome = await harness.coordinator.finalize_with_repair(prompt="Build tracker", repair=repair)
    assert len(feedback) == 1
    assert "Could not find a production build" in feedback[0]
    assert "Repair the test/manifest" in feedback[0]
    assert harness.roles.count(ProjectCellCommandRole.FULL_BUILD) == (2 if change_source else 1)
    expected = MaxFinalizationStatus.COMPLETE if change_source else MaxFinalizationStatus.NEEDS_EDIT
    assert outcome.status is expected
    async with async_sessionmaker(test_engine)() as session:
        red_builds = await session.scalar(select(func.count(ProjectCellProofResult.id)).where(
            ProjectCellProofResult.dimension == "full_build",
            ProjectCellProofResult.outcome == "red",
        ))
        assert red_builds == 1


async def test_unclassified_service_failure_does_not_trigger_model_repair(db_session, test_engine):
    harness = await _new_harness(db_session, test_engine)
    original_role = harness.coordinator.executor.run_role

    async def run_role(role, operation_id):
        result = await original_role(role, operation_id)
        if role is ProjectCellCommandRole.FULL_BUILD:
            return replace(result, ok=False, redacted_detail="service web readiness failed: 502")
        return result

    async def repair(detail):
        raise AssertionError("infrastructure failure must not trigger speculative editing")

    harness.coordinator.executor = replace(harness.coordinator.executor, run_role=run_role)
    outcome = await harness.coordinator.finalize_with_repair(prompt="Build tracker", repair=repair)
    assert outcome.status is MaxFinalizationStatus.FAILED
