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
