from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.errors import ApiError
from omnia_api.core.security import create_access_token
from omnia_api.models.restoration import Restoration
from omnia_api.schemas.restoration import RestoreApplyRequest
from omnia_api.services import repo
from omnia_api.services import restorations as service
from omnia_api.services.restoration_reconciliation import reconcile_due_restorations
from tests.test_restorations import FakeRuntime, restoration_fixture


@pytest.fixture(autouse=True)
def ready_source_resources(monkeypatch):
    from omnia_api.services import project_cell_runtime

    async def resources(_workspace_id):
        return SimpleNamespace(state="resources_ready")

    monkeypatch.setattr(project_cell_runtime, "_get_cell_resources", resources)


async def test_service_detail_read_needs_only_read_models_and_never_a_runtime():
    from omnia_api.models.project import Project

    owner_id, project_id, operation_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, owner_id=owner_id)
    operation = SimpleNamespace(
        id=operation_id,
        project_id=project_id,
        owner_id=owner_id,
        source_version_id=uuid4(),
        source_snapshot_id=uuid4(),
        base_draft_snapshot_id=uuid4(),
        state="preparing",
        phase="prepare",
        updated_at=datetime.now(UTC),
        revision=1,
        candidate_id=None,
        report=None,
        runtime_result=None,
        source_binding=None,
        source_binding_digest=None,
        apply_digest=None,
        applied_version_id=None,
        applied_snapshot_id=None,
        error=None,
    )
    session = AsyncMock()

    async def get(model, identifier):
        return project if model is Project and identifier == project_id else operation

    session.get.side_effect = get

    result = await service.get_restoration(session, project_id, owner_id, operation_id)

    assert result.id == operation_id and result.state == "preparing"
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_service_list_authorization_never_enters_the_write_lock_domain():
    owner_id, project_id = uuid4(), uuid4()
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(id=project_id, owner_id=owner_id)
    session.scalars.return_value = SimpleNamespace(all=lambda: [])

    result = await service.list_operations(session, project_id, owner_id)

    assert result == []
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_public_detail_get_is_a_pure_projection_across_one_hundred_reads(
    client, db_session, monkeypatch
):
    from omnia_api.routers import restorations as routes

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    row = await db_session.get(Restoration, operation.id)
    row.state = "preparing"
    row.phase = "prepare"
    row.request_payload = {}
    row.planned_commit_sha = None
    await db_session.commit()
    await db_session.refresh(row)
    before = (row.revision, row.updated_at, row.runtime_revision, project.current_snapshot_id)

    def forbidden_runtime():
        raise AssertionError("public GET must not construct an orchestrator client")

    def forbidden_git(*_args, **_kwargs):
        raise AssertionError("public GET must not mutate restoration Git state")

    monkeypatch.setattr(routes, "HttpRestorationRuntime", forbidden_runtime)
    monkeypatch.setattr(repo, "prepare_restore_commit", forbidden_git)
    monkeypatch.setattr(repo, "activate_restore_commit", forbidden_git)
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    path = f"/api/projects/{project.id}/restorations/{operation.id}"

    for _ in range(100):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json()["state"] == "preparing"

    db_session.expire_all()
    persisted = await db_session.get(Restoration, operation.id)
    persisted_project = await db_session.get(type(project), project.id)
    assert persisted is not None and persisted_project is not None
    assert (
        persisted.revision,
        persisted.updated_at,
        persisted.runtime_revision,
        persisted_project.current_snapshot_id,
    ) == before


async def test_public_detail_get_never_observes_or_redispatches_runtime_404(
    client, db_session, monkeypatch
):
    from omnia_api.routers import restorations as routes

    class MissingRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.status_calls = 0

        async def status(self, request):
            self.status_calls += 1
            raise ApiError("not_found", "operation not found", 404)

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    runtime = MissingRuntime()
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    row = await db_session.get(Restoration, operation.id)
    row.state = "reconciling"
    row.phase = "cancel"
    await db_session.commit()
    monkeypatch.setattr(routes, "HttpRestorationRuntime", lambda: runtime)
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}

    response = await client.get(
        f"/api/projects/{project.id}/restorations/{operation.id}", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["state"] == "reconciling"
    assert runtime.status_calls == 0
    assert runtime.cancel_calls == 0


async def test_public_list_uses_no_advisory_or_row_write_locks(
    db_session, test_engine
):
    owner, project, *_ = await restoration_fixture(db_session)
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(test_engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        await service.list_operations(db_session, project.id, owner.id)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", record_statement)

    assert statements
    assert all("pg_advisory" not in statement for statement in statements)
    assert all("for update" not in statement for statement in statements)


async def test_worker_owns_lost_dispatch_replay_and_completed_apply(
    db_session, test_engine, monkeypatch
):
    from omnia_api.services import restoration_reconciliation as reconciliation

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    runtime.lose_apply_reply = True
    pending = await service.apply_restoration(
        db_session,
        project.id,
        owner.id,
        operation.id,
        RestoreApplyRequest(
            report_revision=1,
            expected_draft_snapshot_id=current.id,
            idempotency_key="worker-completes-apply",
        ),
        runtime,
    )
    assert pending.state == "reconciling"

    async def forbidden_public_get(*_args, **_kwargs):
        raise AssertionError("worker must use the worker-only advance path")

    monkeypatch.setattr(
        reconciliation, "get_restoration", forbidden_public_get, raising=False
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    later = datetime.now(UTC) + timedelta(seconds=60)

    assert await reconcile_due_restorations(factory, runtime, now=later) == 1
    db_session.expire_all()
    persisted = await db_session.get(Restoration, operation.id)
    persisted_project = await db_session.get(type(project), project.id)
    assert persisted is not None and persisted_project is not None
    assert persisted.state == "completed"
    assert persisted.applied_snapshot_id == persisted_project.current_snapshot_id


async def test_worker_identical_receipt_is_a_semantic_noop(db_session):
    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    row = await db_session.get(Restoration, operation.id)
    runtime.result = runtime.result.model_copy(
        update={"state": "checking", "phase": "checking", "can_apply": False}
    )
    row.state = "checking"
    row.phase = "checking"
    row.runtime_result = runtime.result.model_dump(mode="json")
    await db_session.commit()
    before = (row.revision, row.updated_at, row.runtime_revision, row.reconcile_attempts)

    await service.advance_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )

    await db_session.refresh(row)
    assert (row.revision, row.updated_at, row.runtime_revision, row.reconcile_attempts) == before


async def test_same_ready_receipt_repairs_a_late_unconfirmed_projection(db_session):
    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    row = await db_session.get(Restoration, operation.id)
    row.state = "reconciling"
    row.error = "Runtime result is not confirmed; checking the same operation"
    await db_session.commit()
    before_revision = row.revision

    repaired = await service.advance_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )

    assert repaired.state == "ready" and repaired.error is None
    assert repaired.revision == before_revision + 1


async def test_same_completed_receipt_retries_only_unfinished_local_projection(db_session):
    owner, project, _, current, _, workspace, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    real_apply = runtime.apply

    async def apply_then_drift_fence(payload):
        result = await real_apply(payload)
        workspace.fencing_epoch += 1
        return result

    runtime.apply = apply_then_drift_fence
    pending = await service.apply_restoration(
        db_session,
        project.id,
        owner.id,
        operation.id,
        RestoreApplyRequest(
            report_revision=1,
            expected_draft_snapshot_id=current.id,
            idempotency_key="completed-receipt-before-local-projection",
        ),
        runtime,
    )
    assert pending.state == "reconciling"
    row = await db_session.get(Restoration, operation.id)
    before = (row.revision, row.updated_at)

    still_pending = await service.advance_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    await db_session.refresh(row)
    assert still_pending.state == "reconciling"
    assert (row.revision, row.updated_at) == before

    workspace.fencing_epoch = row.fencing_epoch
    await db_session.commit()
    completed = await service.advance_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    assert completed.state == "completed"
    assert completed.applied_snapshot_id is not None
