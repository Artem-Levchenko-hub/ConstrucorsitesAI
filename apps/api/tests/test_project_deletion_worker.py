"""One owner DELETE persists intent; independent worker sessions finish it safely."""

# ruff: noqa: F811 - pytest injects the shared imported fixtures by name.

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_projects_delete import (  # noqa: F401 - shared authenticated ASGI fixtures
    _cell_project,
    _destroyed_cell,
    _make_project,
    _make_user,
    as_user,
    fake_teardown,
)
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.services.orchestrator_client import (
    HttpProjectCellOrchestratorClient,
    OrchestratorUnavailable,
)
from yleum_api.services.project_deletion import (
    advance_project_deletions,
    finish_pending_project_deletion,
)

pytestmark = pytest.mark.asyncio


async def test_one_delete_finishes_after_lost_reply_without_browser_retry(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    owner, project, workspace = await _cell_project(db_session)
    project_id, workspace_id = project.id, workspace.id
    as_user(owner)
    requests = []

    async def destroy(_self, request):
        requests.append(request)
        if len(requests) == 1:
            raise OrchestratorUnavailable("reply lost after dispatch")
        return _destroyed_cell(request)

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    response = await client.delete(f"/api/projects/{project_id}")
    assert response.status_code == 202, response.text
    assert response.json() == {"state": "deleting", "project_id": str(project_id)}
    assert requests == [] and fake_teardown["repo"] == []

    # A fresh session/worker process discovers the committed intent; there is no
    # second browser DELETE, in-memory background task or dependency override.
    assert await advance_project_deletions(test_engine) == 0
    async with async_sessionmaker(test_engine)() as observed:
        assert await observed.get(Project, project_id) is not None
        assert (await observed.get(ProjectCellWorkspace, workspace_id)).state == "deleting"
        operation = await observed.scalar(select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace_id,
        ))
        assert operation.status == "indeterminate"
    assert fake_teardown["repo"] == []
    assert await advance_project_deletions(test_engine) == 1
    assert len(requests) == 2
    assert requests[0].to_wire_json() == requests[1].to_wire_json()
    db_session.expunge_all()
    assert (await client.get(f"/api/projects/{project_id}")).status_code == 404


async def test_permanent_outage_keeps_tombstone_repo_and_project(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    owner, project, workspace = await _cell_project(db_session)
    project_id, workspace_id = project.id, workspace.id
    as_user(owner)

    async def offline(*_args):
        raise OrchestratorUnavailable("controller unavailable")

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", offline)
    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "observe_resources", offline)
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 202
    for _ in range(3):
        assert await advance_project_deletions(test_engine) == 0
    async with async_sessionmaker(test_engine)() as observed:
        assert await observed.get(Project, project_id) is not None
        tombstone = await observed.get(ProjectCellWorkspace, workspace_id)
        assert tombstone.state == "deleting" and tombstone.deleted_at is not None
    assert fake_teardown["repo"] == [] and fake_teardown["destroy"] == []


async def test_two_executors_do_not_dispatch_twice_across_internal_commits(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    owner, project, _workspace = await _cell_project(db_session)
    project_id = project.id
    as_user(owner)
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def destroy(_self, request):
        requests.append(request)
        # The SQL operation was already committed running before dispatch.
        entered.set()
        await release.wait()
        return _destroyed_cell(request)

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 202
    first = asyncio.create_task(finish_pending_project_deletion(test_engine, project_id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert await finish_pending_project_deletion(test_engine, project_id) == "busy"
        assert len(requests) == 1 and fake_teardown["repo"] == []
    finally:
        release.set()
        assert await asyncio.wait_for(first, timeout=5) == "completed"
    assert len(requests) == 1


async def test_worker_restart_recovers_its_running_sql_envelope_only(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    from yleum_api.services.project_cells import (
        claim_cell_operation_committed,
        reserve_cell_operation,
    )

    owner, project, workspace = await _cell_project(db_session)
    project_id, workspace_id = project.id, workspace.id
    as_user(owner)
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 202
    operation, _ = await reserve_cell_operation(
        db_session, workspace_id=workspace_id, generation_run_id=None, kind="destroy",
        idempotency_key=f"project-delete:{workspace_id}", request={},
    )
    operation_id = operation.id
    await db_session.commit()
    claimed = await claim_cell_operation_committed(
        async_sessionmaker(test_engine, expire_on_commit=False), operation_id,
    )
    requests = []

    async def destroy(_self, request):
        requests.append(request)
        assert request.operation_id == claimed.operation_id
        assert request.fencing_epoch == claimed.fencing_epoch
        assert request.request_digest == claimed.request_digest
        return _destroyed_cell(request)

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    assert await finish_pending_project_deletion(test_engine, project_id) == "completed"
    assert len(requests) == 1


async def test_legacy_teardown_retry_does_not_replay_completed_cell_destroy(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    from yleum_api.services import orchestrator_client

    owner, project, _workspace = await _cell_project(db_session)
    project_id = project.id
    as_user(owner)
    controls, legacy = [], []

    async def destroy(_self, request):
        controls.append(request)
        return _destroyed_cell(request)

    async def legacy_destroy(*args):
        legacy.append(args)
        if len(legacy) == 1:
            raise OrchestratorUnavailable("legacy teardown reply lost")
        return {"state": "destroyed"}

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    monkeypatch.setattr(orchestrator_client, "destroy", legacy_destroy)
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 202
    assert await advance_project_deletions(test_engine) == 0
    assert fake_teardown["repo"] == []
    assert await advance_project_deletions(test_engine) == 1
    assert len(controls) == 1 and len(legacy) == 2


async def test_connection_loss_cannot_finalize_data_and_next_claim_reconciles(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    from yleum_api.services import project_deletion

    owner, project, _workspace = await _cell_project(db_session)
    project_id = project.id
    as_user(owner)
    claims, requests = [], []
    original_claim = project_deletion.project_deletion_claim

    @asynccontextmanager
    async def capture_claim(engine, target):
        async with original_claim(engine, target) as claim:
            if claim is not None:
                claims.append(claim)
            yield claim

    async def destroy(_self, request):
        requests.append(request)
        if len(requests) == 1:
            # Kill only this executor's known private-test PostgreSQL backend,
            # after running was committed and while its external call is active.
            async with test_engine.begin() as other:
                assert await other.scalar(text("SELECT pg_terminate_backend(:pid)"), {
                    "pid": claims[-1].backend_pid,
                })
        return _destroyed_cell(request)

    monkeypatch.setattr(project_deletion, "project_deletion_claim", capture_claim)
    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 202
    assert await finish_pending_project_deletion(test_engine, project_id) == "pending"
    assert fake_teardown["repo"] == [] and fake_teardown["destroy"] == []
    async with async_sessionmaker(test_engine)() as observed:
        assert await observed.get(Project, project_id) is not None
    assert await finish_pending_project_deletion(test_engine, project_id) == "completed"
    assert len(requests) in (1, 2)
    if len(requests) == 2:
        assert requests[0].to_wire_json() == requests[1].to_wire_json()


async def test_replay_connection_loss_cannot_persist_or_advance_a_new_fence(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    from yleum_api.services import project_deletion
    from yleum_api.services.project_cells import (
        claim_cell_operation_committed,
        mark_cell_operation_indeterminate,
        reserve_cell_operation,
    )

    owner, project, workspace = await _cell_project(db_session)
    project_id, workspace_id = project.id, workspace.id
    as_user(owner)
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 202
    operation, _ = await reserve_cell_operation(
        db_session, workspace_id=workspace_id, generation_run_id=None, kind="destroy",
        idempotency_key=f"project-delete:{workspace_id}", request={},
    )
    operation_id = operation.id
    await db_session.commit()
    original = await claim_cell_operation_committed(
        async_sessionmaker(test_engine, expire_on_commit=False), operation_id,
    )
    db_session.expunge_all()
    await mark_cell_operation_indeterminate(db_session, operation_id, "previous reply lost")
    await db_session.commit()
    claims, requests, observations = [], [], []
    original_claim = project_deletion.project_deletion_claim

    @asynccontextmanager
    async def capture_claim(engine, target):
        async with original_claim(engine, target) as claim:
            if claim is not None:
                claims.append(claim)
            yield claim

    async def destroy(_self, request):
        requests.append(request)
        assert request.operation_id == original.operation_id
        assert request.fencing_epoch == original.fencing_epoch
        assert request.request_digest == original.request_digest
        if len(requests) == 1:
            async with test_engine.begin() as other:
                assert await other.scalar(text("SELECT pg_terminate_backend(:pid)"), {
                    "pid": claims[-1].backend_pid,
                })
            raise OrchestratorUnavailable("replay response lost with ownership connection")
        return _destroyed_cell(request)

    async def observe(*args):
        observations.append(args)
        raise AssertionError("An old claim must not reserve or dispatch a reconcile")

    monkeypatch.setattr(project_deletion, "project_deletion_claim", capture_claim)
    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "observe_resources", observe)
    assert await finish_pending_project_deletion(test_engine, project_id) == "pending"
    async with async_sessionmaker(test_engine)() as observed:
        rows = list((await observed.scalars(select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace_id,
        ))).all())
        assert len(rows) == 1 and rows[0].id == operation_id
        # The reclaim was committed before the lost connection. Neither terminal
        # fallback nor a reconcile reservation may mutate SQL under a new session.
        assert rows[0].status == "running"
        assert rows[0].fencing_epoch == original.fencing_epoch
        assert (await observed.get(ProjectCellWorkspace, workspace_id)).fencing_epoch == (
            original.fencing_epoch
        )
        assert await observed.get(Project, project_id) is not None
    assert len(requests) == 1 and observations == []
    assert fake_teardown["repo"] == [] and fake_teardown["destroy"] == []
    assert await finish_pending_project_deletion(test_engine, project_id) == "completed"
    assert len(requests) == 2
    assert requests[0].to_wire_json() == requests[1].to_wire_json()


@pytest.mark.parametrize("winner", ["generation", "restoration", "owner", "cell"])
async def test_no_cell_deletion_rechecks_admission_after_transaction_gap(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch, winner,
):
    from uuid import uuid4

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.restoration import Restoration
    from yleum_api.services import project_deletion

    owner = await _make_user(db_session, f"no-cell-{uuid4().hex}@example.com")
    other_owner = await _make_user(db_session, f"new-owner-{uuid4().hex}@example.com")
    project = await _make_project(db_session, owner, template="max_miniapp")
    await db_session.commit()
    project_id, owner_id, other_owner_id = project.id, owner.id, other_owner.id
    as_user(owner)
    original_claim = project_deletion.project_deletion_claim
    workspace_id = None

    @asynccontextmanager
    async def competing_admission(engine, target):
        nonlocal workspace_id
        # A second valid producer wins the gap after the initial no-Cell commit.
        async with async_sessionmaker(engine, expire_on_commit=False)() as competing:
            await competing.execute(text("SELECT pg_advisory_xact_lock(hashtext(:id))"), {
                "id": str(target),
            })
            current = await competing.get(Project, target, with_for_update=True)
            if winner == "owner":
                current.owner_id = other_owner_id
            elif winner == "generation":
                competing.add(GenerationRun(
                    project_id=target, user_id=owner_id, idempotency_key="racing-generation",
                    prompt_hash="a" * 64, status="running",
                ))
            else:
                cell = ProjectCellWorkspace(
                    project_id=target, owner_id=owner_id, provider="docker_owner_canary",
                    state="ready", fencing_epoch=3,
                )
                competing.add(cell)
                await competing.flush()
                workspace_id = cell.id
                if winner == "restoration":
                    competing.add(Restoration(
                        id=uuid4(), project_id=target, owner_id=owner_id, workspace_id=cell.id,
                        source_version_id=uuid4(), source_snapshot_id=uuid4(),
                        base_draft_snapshot_id=uuid4(), target_commit_sha="a" * 40,
                        base_commit_sha="b" * 40, idempotency_key="racing-restoration",
                        request_digest="c" * 64, state="preparing", phase="prepare",
                        fencing_epoch=3,
                    ))
            await competing.commit()
        async with original_claim(engine, target) as claim:
            yield claim

    monkeypatch.setattr(project_deletion, "project_deletion_claim", competing_admission)
    response = await client.delete(f"/api/projects/{project_id}")
    assert response.status_code == {"owner": 403, "cell": 202}.get(winner, 409), response.text
    if winner == "restoration":
        assert response.json()["error"]["code"] == "restoration_active"
    assert fake_teardown["repo"] == [] and fake_teardown["destroy"] == []
    async with async_sessionmaker(test_engine)() as observed:
        assert await observed.get(Project, project_id) is not None
        if workspace_id is not None:
            cell = await observed.get(ProjectCellWorkspace, workspace_id)
            assert cell.state == ("deleting" if winner == "cell" else "ready")
            assert (cell.deleted_at is not None) == (winner == "cell")


async def test_no_cell_finalizer_keeps_generation_admission_locked_until_commit(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    from uuid import uuid4

    from yleum_api.services import orchestrator_client

    owner = await _make_user(db_session, f"no-cell-lock-{uuid4().hex}@example.com")
    project = await _make_project(db_session, owner, template="max_miniapp")
    await db_session.commit()
    project_id = project.id
    as_user(owner)
    checked = []

    async def destroy(target, _slug):
        async with test_engine.begin() as competing:
            acquired = await competing.scalar(text(
                "SELECT pg_try_advisory_xact_lock(hashtext(:id))"
            ), {"id": str(target)})
        assert acquired is False
        checked.append(target)
        return {"state": "destroyed"}

    monkeypatch.setattr(orchestrator_client, "destroy", destroy)
    response = await client.delete(f"/api/projects/{project_id}")
    assert response.status_code == 204, response.text
    assert checked == [project_id]
    async with async_sessionmaker(test_engine)() as observed:
        assert await observed.get(Project, project_id) is None
        assert await observed.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:id))"), {
            "id": str(project_id),
        }) is True


async def test_active_generation_refuses_admission_before_tombstone_or_effects(
    client, db_session, as_user, fake_teardown, monkeypatch,
):
    from yleum_api.models.generation_run import GenerationRun

    owner, project, workspace = await _cell_project(db_session)
    project_id, workspace_id = project.id, workspace.id
    db_session.add(GenerationRun(
        project_id=project_id, user_id=owner.id, idempotency_key="active-delete-guard",
        prompt_hash="a" * 64, status="running",
    ))
    await db_session.commit()
    as_user(owner)
    control = []

    async def forbidden_control(*args):
        control.append(args)
        raise AssertionError("Guard must precede all external effects")

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", forbidden_control)
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 409
    db_session.expunge_all()
    retained = await db_session.get(ProjectCellWorkspace, workspace_id)
    assert retained.state == "ready" and retained.deleted_at is None
    assert control == [] and fake_teardown["repo"] == []


async def test_deferred_finalizer_preserves_immutable_financial_records(
    client, db_session, test_engine, as_user, fake_teardown, monkeypatch,
):
    from yleum_api.models.billing import BillingAccount
    from yleum_api.models.message import Message
    from yleum_api.models.usage import Usage
    from yleum_api.models.wallet_charge import WalletCharge

    owner, project, _workspace = await _cell_project(db_session)
    project_id = project.id
    billing = BillingAccount(
        scope="personal", personal_user_id=owner.id, created_by_user_id=owner.id,
    )
    message = Message(project_id=project_id, role="assistant", content="Generated app")
    db_session.add_all([billing, message])
    await db_session.flush()
    charge = WalletCharge(
        billing_account_id=billing.id, user_id=owner.id, message_id=message.id,
        amount_rub=Decimal("-3.5000"), balance_after_rub=Decimal("96.5000"),
        description="Generation charge",
    )
    usage = Usage(
        user_id=owner.id, project_id=project_id, message_id=message.id,
        model_id="test-model", tokens_in=10, tokens_out=20, cost_rub=Decimal("0.1000"),
    )
    db_session.add_all([charge, usage])
    await db_session.commit()
    charge_id, usage_id = charge.id, usage.id
    as_user(owner)

    async def destroy(_self, request):
        return _destroyed_cell(request)

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 202
    assert await advance_project_deletions(test_engine) == 1
    async with async_sessionmaker(test_engine)() as observed:
        charge = await observed.get(WalletCharge, charge_id)
        usage = await observed.get(Usage, usage_id)
        assert charge.message_id is None and charge.amount_rub == Decimal("-3.5000")
        assert charge.balance_after_rub == Decimal("96.5000")
        assert usage.project_id is None and usage.message_id is None
        assert usage.cost_rub == Decimal("0.1000")
