"""AI-free lifecycle regressions for the exact generation's version projection."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_version import ProjectVersion
from yleum_api.models.snapshot import Snapshot
from yleum_api.services import generation_runs, project_versions


def _run_and_version(status: str = "running"):
    project_id, run_id, snapshot_id = uuid4(), uuid4(), uuid4()
    run = GenerationRun(
        id=run_id, project_id=project_id, user_id=uuid4(), status=status,
        agent_state={}, assistant_message_id=None,
    )
    version = ProjectVersion(
        id=uuid4(), project_id=project_id, generation_run_id=run_id,
        number=2, status="queued", base_snapshot_id=snapshot_id,
        snapshot_id=snapshot_id, commit_sha="a" * 40, prompt_text="Own request",
    )
    base = Snapshot(id=snapshot_id, project_id=project_id, commit_sha="a" * 40)
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = version
    session.get.return_value = base
    return run, version, base, session


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_terminal_status_updates_queued_projection_in_the_same_transaction(
    monkeypatch, status,
):
    run, version, base, session = _run_and_version()
    seal = AsyncMock()
    monkeypatch.setattr("yleum_api.services.generation_billing.seal_terminal_locked", seal)

    await generation_runs.terminalize_generation_run_locked(
        session, run, status=status, error="provider gateway 502",
    )

    assert run.status == version.status == status
    assert version.snapshot_id == base.id
    assert version.commit_sha == base.commit_sha
    seal.assert_awaited_once_with(session, run)
    session.commit.assert_not_awaited()


async def test_capacity_admission_marks_its_version_running_before_commit(monkeypatch):
    run, version, _, session = _run_and_version("pending")
    session.__aenter__.return_value = session
    session.scalar.side_effect = [run, version]
    monkeypatch.setattr(generation_runs, "note_generation_release", lambda _run: None)

    result = await generation_runs.promote_generation_after_admission(
        Mock(return_value=session), run_id=run.id, dispatch_token=None,
    )

    assert result == "admitted"
    assert run.status == version.status == "running"
    session.commit.assert_awaited_once()


async def test_projection_lookup_is_exact_run_project_and_row_locked(monkeypatch):
    run, version, _, session = _run_and_version()
    monkeypatch.setattr(
        "yleum_api.services.generation_billing.seal_terminal_locked", AsyncMock(),
    )

    await generation_runs.terminalize_generation_run_locked(session, run, status="failed")

    statement = session.scalar.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "project_versions.generation_run_id =" in sql
    assert "project_versions.project_id =" in sql
    assert "FOR UPDATE" in sql
    assert {run.id, run.project_id} <= set(statement.compile().params.values())
    assert version.status == "failed"


@pytest.mark.parametrize(
    ("run_status", "expected"),
    [("pending", "queued"), ("queued_for_capacity", "queued"),
     ("running", "running"), ("cancel_requested", "running"),
     ("failed", "failed"), ("cancelled", "cancelled"), ("completed", "unchanged")],
)
async def test_late_version_allocation_uses_current_run_status(monkeypatch, run_status, expected):
    run, _, _, session = _run_and_version(run_status)
    run.user_message_id = None
    session.scalar.return_value = None
    project = Project(id=run.project_id, current_snapshot_id=None)
    monkeypatch.setattr(project_versions, "_lock", AsyncMock(return_value=2))

    version = await project_versions.ensure_generation_version(session, run, project)

    assert version.status == expected
    assert version.generation_run_id == run.id
    session.commit.assert_not_awaited()


async def test_replayed_allocator_does_not_revive_ready_version_from_stale_run(monkeypatch):
    run, version, _, session = _run_and_version("pending")
    version.status = "ready"
    monkeypatch.setattr(project_versions, "_lock", AsyncMock(return_value=3))

    result = await project_versions.ensure_generation_version(
        session, run, Project(id=run.project_id),
    )

    assert result is version
    assert version.status == "ready"
    session.add.assert_not_called()


async def test_version_allocated_after_completion_resolves_its_actual_final_snapshot(monkeypatch):
    run, _, base, session = _run_and_version("completed")
    run.user_message_id = None
    final = Snapshot(id=uuid4(), project_id=run.project_id, commit_sha="b" * 40)
    run.agent_state = {"snapshot_id": str(final.id)}
    session.scalar.return_value = None

    async def get(_model, object_id):
        return {base.id: base, final.id: final}.get(object_id)

    session.get.side_effect = get
    monkeypatch.setattr(project_versions, "_lock", AsyncMock(return_value=2))

    version = await project_versions.ensure_generation_version(
        session, run, Project(id=run.project_id, current_snapshot_id=base.id),
    )

    assert version.status == "ready"
    assert version.snapshot_id == final.id
    assert version.commit_sha == final.commit_sha


async def test_missing_version_is_not_allocated_by_lifecycle():
    run, _, _, session = _run_and_version("failed")
    session.scalar.return_value = None

    assert await project_versions.sync_generation_version_locked(session, run) is None

    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("same_commit", [False, True])
async def test_completed_projection_uses_only_its_final_snapshot_provenance(same_commit):
    run, version, base, session = _run_and_version("completed")
    final = Snapshot(
        id=uuid4(), project_id=run.project_id,
        commit_sha=base.commit_sha if same_commit else "b" * 40,
    )
    run.agent_state = {"snapshot_id": str(final.id)}

    async def get(_model, object_id):
        return {base.id: base, final.id: final}.get(object_id)

    session.get.side_effect = get
    await project_versions.sync_generation_version_locked(session, run)

    assert version.status == ("unchanged" if same_commit else "ready")
    assert version.snapshot_id == (base.id if same_commit else final.id)
    assert version.commit_sha == final.commit_sha


async def test_foreign_final_snapshot_never_becomes_ready_output():
    run, version, base, session = _run_and_version("completed")
    foreign = Snapshot(id=uuid4(), project_id=uuid4(), commit_sha="b" * 40)
    run.agent_state = {"snapshot_id": str(foreign.id)}

    async def get(_model, object_id):
        return {base.id: base, foreign.id: foreign}.get(object_id)

    session.get.side_effect = get
    await project_versions.sync_generation_version_locked(session, run)

    assert version.status == "unchanged"
    assert version.snapshot_id == base.id
    assert version.commit_sha == base.commit_sha


async def test_completion_rejection_precedes_any_projection_change(monkeypatch):
    run, version, _, session = _run_and_version()
    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_acceptance.acceptance_required",
        lambda _run: False,
    )
    monkeypatch.setattr(
        "yleum_api.services.generation.completion_release.generation_release_pending",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_acceptance.require_terminal_acceptance",
        AsyncMock(side_effect=ValueError("acceptance not confirmed")),
    )
    with pytest.raises(ValueError, match="acceptance not confirmed"):
        await generation_runs.terminalize_generation_run_locked(session, run, status="completed")

    assert run.status == "running"
    assert version.status == "queued"
    session.scalar.assert_not_awaited()


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "completed"])
async def test_idempotent_finalizer_repairs_projection_without_reterminalizing(
    monkeypatch, terminal,
):
    run, version, _, session = _run_and_version(terminal)
    finished = datetime(2026, 11, 6, tzinfo=UTC)
    run.finished_at = finished
    run.error = "original diagnostic"
    seal = AsyncMock()
    monkeypatch.setattr("yleum_api.services.generation_billing.seal_terminal_locked", seal)
    monkeypatch.setattr(
        generation_runs, "_lock_generation_for_completion", AsyncMock(return_value=run),
    )

    result = await generation_runs._finalize_generation_run(session, run.id)

    assert result == run.status == terminal
    assert run.finished_at is finished
    assert run.error == "original diagnostic"
    assert version.status == ("unchanged" if terminal == "completed" else terminal)
    seal.assert_not_awaited()
    session.commit.assert_awaited_once()


async def test_late_running_callback_repairs_failed_projection_without_reviving_run(monkeypatch):
    run, version, base, session = _run_and_version("failed")
    run.finished_at = datetime(2026, 11, 6, tzinfo=UTC)
    finished = run.finished_at
    session.__aenter__.return_value = session

    async def get(model, _object_id, **_kwargs):
        return run if model is GenerationRun else base

    session.get.side_effect = get
    monkeypatch.setattr("yleum_api.core.db.get_engine", lambda: None)
    monkeypatch.setattr(
        generation_runs, "async_sessionmaker", Mock(return_value=Mock(return_value=session)),
    )

    await generation_runs.set_generation_run_status(run.id, "running")

    assert run.status == version.status == "failed"
    assert run.finished_at is finished
    session.commit.assert_awaited_once()


async def test_stale_capacity_dispatch_does_not_touch_version():
    run, version, _, session = _run_and_version("queued_for_capacity")
    session.__aenter__.return_value = session
    session.scalar.return_value = run

    result = await generation_runs.promote_generation_after_admission(
        Mock(return_value=session), run_id=run.id, dispatch_token=uuid4(),
    )

    assert result == "lost"
    assert run.status == "queued_for_capacity"
    assert version.status == "queued"
    assert session.scalar.await_count == 1
    session.commit.assert_not_awaited()


async def test_explicit_reconcile_requires_exact_current_owner_run_and_project():
    run, version, _, session = _run_and_version("failed")
    session.scalar.side_effect = [run, version]
    original_state = run.agent_state.copy()

    result = await project_versions.reconcile_generation_version(
        session, project_id=run.project_id, run_id=run.id, owner_id=run.user_id,
    )

    assert result is version
    assert version.status == "failed"
    assert run.status == "failed" and run.agent_state == original_state
    query = session.scalar.await_args_list[0].args[0]
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "generation_runs.id =" in sql
    assert "generation_runs.project_id =" in sql
    assert "generation_runs.user_id =" in sql
    assert "projects.owner_id =" in sql
    assert "FOR UPDATE OF generation_runs" in sql
    assert {run.id, run.project_id, run.user_id} <= set(query.compile().params.values())
    session.commit.assert_not_awaited()
    session.add.assert_not_called()


async def test_unowned_or_missing_reconcile_target_leaves_all_versions_unchanged():
    run, version, _, session = _run_and_version("failed")
    session.scalar.return_value = None

    result = await project_versions.reconcile_generation_version(
        session, project_id=run.project_id, run_id=run.id, owner_id=uuid4(),
    )

    assert result is None
    assert version.status == "queued"
    assert session.scalar.await_count == 1
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
