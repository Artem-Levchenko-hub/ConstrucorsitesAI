"""Delete-project teardown + owner-scoping (P1).

Covers the `DELETE /api/projects/{id}` contract: owner-scoping (404 missing /
403 foreign), runtime teardown only for container-backed templates, git-repo
removal, DB cascade, and idempotency. Orchestrator + MinIO side effects are
faked so the test stays a fast unit of the handler logic (the real teardown is
exercised live in E2E).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.usage import Usage
from omnia_api.models.user import User

pytestmark = pytest.mark.asyncio


async def _make_user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _make_project(
    session: AsyncSession, owner: User, *, template: str = "blank"
) -> Project:
    project = Project(
        owner_id=owner.id,
        name="Test Project",
        slug=f"test-{uuid.uuid4().hex[:6]}",
        template=template,
    )
    session.add(project)
    await session.flush()
    return project


@pytest_asyncio.fixture
async def as_user(db_session: AsyncSession):
    """Authenticate the test client as a chosen User by overriding the auth dep."""

    def _login(user: User) -> None:
        async def _override() -> User:
            return user

        app.dependency_overrides[get_current_user] = _override

    yield _login
    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def fake_teardown(monkeypatch):
    """Record orchestrator.destroy calls and stub MinIO repo deletion."""
    calls: dict[str, list] = {"destroy": [], "repo": []}

    async def _destroy(project_id, slug):
        calls["destroy"].append((project_id, slug))
        return {"state": "destroyed"}

    def _delete_repo(project_id):
        calls["repo"].append(project_id)

    monkeypatch.setattr(
        "omnia_api.services.orchestrator_client.destroy", _destroy
    )
    monkeypatch.setattr("omnia_api.services.repo.delete_repo", _delete_repo)
    return calls


async def test_delete_static_project_skips_orchestrator(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner@example.com")
    project = await _make_project(db_session, owner, template="landing")
    await db_session.commit()
    as_user(owner)

    resp = await client.delete(f"/api/projects/{project.id}")

    assert resp.status_code == 204
    assert await db_session.get(Project, project.id) is None
    # Static template → no container to tear down.
    assert fake_teardown["destroy"] == []
    # Git repo is always cleaned up.
    assert fake_teardown["repo"] == [project.id]


async def test_delete_container_project_tears_down_runtime(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner2@example.com")
    project = await _make_project(db_session, owner, template="nextjs_entities")
    slug = project.slug
    await db_session.commit()
    as_user(owner)

    resp = await client.delete(f"/api/projects/{project.id}")

    assert resp.status_code == 204
    assert await db_session.get(Project, project.id) is None
    assert fake_teardown["destroy"] == [(project.id, slug)]
    assert fake_teardown["repo"] == [project.id]


async def test_delete_cascades_messages(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner3@example.com")
    project = await _make_project(db_session, owner)
    msg = Message(project_id=project.id, role="user", content="hi")
    db_session.add(msg)
    await db_session.commit()
    msg_id = msg.id
    as_user(owner)

    resp = await client.delete(f"/api/projects/{project.id}")

    assert resp.status_code == 204
    assert await db_session.get(Message, msg_id) is None


async def test_delete_project_preserves_usage_and_clears_project_refs(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    # Production accumulated these FKs in this order through migrations. The
    # resulting PostgreSQL trigger order differs from fresh metadata.create_all
    # and exposes the multi-path SET NULL/CASCADE race this test protects.
    for ddl in (
        "ALTER TABLE usage DROP CONSTRAINT fk_usage_project_id_projects",
        "ALTER TABLE generation_runs "
        "DROP CONSTRAINT fk_generation_runs_project_id_projects",
        "ALTER TABLE usage ADD CONSTRAINT fk_usage_project_id_projects "
        "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL",
        "ALTER TABLE generation_runs "
        "ADD CONSTRAINT fk_generation_runs_project_id_projects "
        "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE",
    ):
        await db_session.execute(text(ddl))
    owner = await _make_user(db_session, "usage-owner@example.com")
    project = await _make_project(db_session, owner, template="nextjs_entities")
    message = Message(project_id=project.id, role="assistant", content="done")
    db_session.add(message)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=message.id,
        idempotency_key=str(uuid.uuid4()),
        prompt_hash="prompt-hash",
        status="completed",
        response_mode="build",
        agent_state={},
    )
    db_session.add(run)
    await db_session.flush()
    usage = Usage(
        user_id=owner.id,
        project_id=project.id,
        message_id=message.id,
        run_id=run.id,
        model_id="test-model",
        tokens_in=10,
        tokens_out=20,
        cost_rub=Decimal("0.1000"),
    )
    db_session.add(usage)
    await db_session.commit()
    usage_id = usage.id
    db_session.expunge_all()
    as_user(owner)

    response = await client.delete(f"/api/projects/{project.id}")

    assert response.status_code == 204
    assert await db_session.get(Project, project.id) is None
    db_session.expire_all()
    preserved_usage = await db_session.get(Usage, usage_id)
    assert preserved_usage is not None
    assert preserved_usage.project_id is None
    assert preserved_usage.message_id is None
    assert preserved_usage.run_id is None


async def test_delete_foreign_project_forbidden(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner4@example.com")
    other = await _make_user(db_session, "intruder@example.com")
    project = await _make_project(db_session, owner)
    await db_session.commit()
    as_user(other)

    resp = await client.delete(f"/api/projects/{project.id}")

    assert resp.status_code == 403
    # Untouched — still there, no teardown ran.
    assert await db_session.get(Project, project.id) is not None
    assert fake_teardown["destroy"] == []
    assert fake_teardown["repo"] == []


async def test_delete_missing_project_not_found(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner5@example.com")
    await db_session.commit()
    as_user(owner)

    resp = await client.delete(f"/api/projects/{uuid.uuid4()}")

    assert resp.status_code == 404


async def test_delete_is_idempotent_second_call_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner6@example.com")
    project = await _make_project(db_session, owner, template="nextjs_entities")
    await db_session.commit()
    as_user(owner)

    first = await client.delete(f"/api/projects/{project.id}")
    second = await client.delete(f"/api/projects/{project.id}")

    assert first.status_code == 204
    assert second.status_code == 404


async def _cell_project(session: AsyncSession) -> tuple[User, Project, ProjectCellWorkspace]:
    owner = await _make_user(session, f"delete-cell-{uuid.uuid4().hex}@example.com")
    project = await _make_project(session, owner, template="max_miniapp")
    workspace = ProjectCellWorkspace(
        project_id=project.id, owner_id=owner.id, provider="docker_owner_canary",
        state="ready", fencing_epoch=3,
    )
    session.add(workspace)
    await session.commit()
    return owner, project, workspace


def _destroyed_cell(request):
    from omnia_api.services.orchestrator_client import ProjectCellResourceResponse

    return ProjectCellResourceResponse(
        workspace_id=request.workspace_id, state="retained", provider_ref="deleted-cell",
        fencing_epoch=request.fencing_epoch, checkpoint_ref="final-retained-backup",
        has_workspace=True, has_agent_home=True, has_postgres=True, has_redis=True,
    )


async def test_delete_cell_confirms_teardown_before_removing_project(
    client, db_session, as_user, fake_teardown, monkeypatch,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from omnia_api.services.orchestrator_client import HttpProjectCellOrchestratorClient

    owner, project, workspace = await _cell_project(db_session)
    project_id, workspace_id = project.id, workspace.id
    as_user(owner)
    physical = {"running": True, "reserved_cpu": 4.2}

    async def destroy(_self, request):
        assert request.workspace_id == workspace_id and request.kind == "destroy"
        async with async_sessionmaker(db_session.bind)() as other_session:
            tombstone = await other_session.get(ProjectCellWorkspace, workspace_id)
            assert tombstone.state == "deleting" and tombstone.deleted_at is not None
            assert await other_session.get(Project, project_id) is not None
        physical.update(running=False, reserved_cpu=0)
        return _destroyed_cell(request)

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    response = await client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 204, response.text
    assert physical == {"running": False, "reserved_cpu": 0}
    db_session.expire_all()
    assert await db_session.get(Project, project_id) is None
    assert await db_session.get(ProjectCellWorkspace, workspace_id) is None


async def test_delete_cell_failure_preserves_project_and_retries_same_operation(
    client, db_session, as_user, fake_teardown, monkeypatch,
):
    from sqlalchemy import select

    from omnia_api.services.orchestrator_client import (
        HttpProjectCellOrchestratorClient,
        OrchestratorUnavailable,
    )

    owner, project, workspace = await _cell_project(db_session)
    project_id, workspace_id = project.id, workspace.id
    as_user(owner)
    envelopes = []

    async def destroy(_self, request):
        envelopes.append((request.operation_id, request.fencing_epoch, request.request_digest))
        if len(envelopes) == 1:
            raise OrchestratorUnavailable("reply lost after dispatch")
        return _destroyed_cell(request)

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    response = await client.delete(f"/api/projects/{project_id}")
    assert response.status_code == 503, response.text
    assert await db_session.get(Project, project_id) is not None
    assert fake_teardown["repo"] == []
    db_session.expire_all()
    pending = await db_session.scalar(select(ProjectCellOperation).where(
        ProjectCellOperation.workspace_id == workspace_id,
    ))
    assert pending.status == "indeterminate"
    assert (await db_session.get(ProjectCellWorkspace, workspace_id)).state == "deleting"
    await db_session.refresh(owner)

    response = await client.delete(f"/api/projects/{project_id}")
    assert response.status_code == 204, response.text
    assert len(envelopes) == 2 and envelopes[0] == envelopes[1]
    assert await db_session.get(Project, project_id) is None


async def test_deleting_cell_rejects_old_preview_start(
    client, db_session, as_user, fake_teardown, monkeypatch,
):
    from datetime import UTC, datetime

    from omnia_api.services import orchestrator_client

    owner, project, workspace = await _cell_project(db_session)
    workspace.state = "deleting"
    workspace.deleted_at = datetime.now(UTC)
    await db_session.commit()
    as_user(owner)
    external_requests = []

    async def deny_external(*args, **kwargs):
        external_requests.append(args)
        raise orchestrator_client.OrchestratorUnavailable("deleting Cell reached controller")

    monkeypatch.setattr(orchestrator_client, "_request", deny_external)
    response = await client.post(f"/api/projects/{project.id}/runtime/start")
    assert response.status_code == 409, response.text
    assert external_requests == []


@pytest.mark.parametrize("entrypoint", ["wake", "generation"])
async def test_deleting_cell_cannot_accept_new_work(db_session, entrypoint):
    from datetime import UTC, datetime

    from omnia_api.services.project_cells import (
        ProjectCellStateConflict,
        get_or_create_workspace,
        reserve_cell_operation,
    )

    owner, project, workspace = await _cell_project(db_session)
    workspace.state = "deleting"
    workspace.deleted_at = datetime.now(UTC)
    run = GenerationRun(
        project_id=project.id, user_id=owner.id, idempotency_key=uuid.uuid4().hex,
        prompt_hash="a" * 64, status="running",
    )
    db_session.add(run)
    await db_session.commit()
    with pytest.raises(ProjectCellStateConflict, match="delet"):
        if entrypoint == "wake":
            await reserve_cell_operation(
                db_session, workspace_id=workspace.id, generation_run_id=None,
                kind="wake", idempotency_key="stale-preview-request", request={},
            )
        else:
            await get_or_create_workspace(db_session, project=project, user=owner, run=run)


async def test_delete_preserves_wallet_charges_without_deleted_message_reference(
    client, db_session, as_user, fake_teardown,
):
    from omnia_api.models.billing import BillingAccount
    from omnia_api.models.wallet_charge import WalletCharge

    owner = await _make_user(db_session, "delete-charged-project@example.com")
    project = await _make_project(db_session, owner)
    billing = BillingAccount(
        scope="personal", personal_user_id=owner.id, created_by_user_id=owner.id,
    )
    message = Message(project_id=project.id, role="assistant", content="Generated app")
    db_session.add_all([billing, message])
    await db_session.flush()
    charge = WalletCharge(
        billing_account_id=billing.id, user_id=owner.id, message_id=message.id,
        amount_rub=Decimal("-3.5000"), balance_after_rub=Decimal("96.5000"),
        description="Generation charge",
    )
    db_session.add(charge)
    await db_session.commit()
    project_id, charge_id = project.id, charge.id
    as_user(owner)

    response = await client.delete(f"/api/projects/{project_id}")
    assert response.status_code == 204, response.text
    db_session.expire_all()
    retained = await db_session.get(WalletCharge, charge_id)
    assert retained is not None and retained.message_id is None
    assert retained.amount_rub == Decimal("-3.5000")
    assert retained.balance_after_rub == Decimal("96.5000")
    assert await db_session.get(Project, project_id) is None


@pytest.mark.parametrize("lost_observation", [False, True])
async def test_delete_recovers_partial_docker_failure_with_fenced_observation(
    client, db_session, as_user, fake_teardown, monkeypatch, lost_observation,
):
    from dataclasses import replace

    from omnia_api.services.orchestrator_client import (
        HttpProjectCellOrchestratorClient,
        OrchestratorBadRequest,
        OrchestratorUnavailable,
    )

    owner, project, _workspace = await _cell_project(db_session)
    project_id = project.id
    as_user(owner)
    destroys, observations = [], []
    physical = {"containers": 2, "confirmed_observation_fence": None}

    async def destroy(_self, request):
        destroys.append(request)
        if len(destroys) == 1:
            physical["containers"] = 1
            raise OrchestratorUnavailable("temporary Docker removal failure")
        if request.operation_id == destroys[0].operation_id:
            assert request.fencing_epoch == destroys[0].fencing_epoch
            raise OrchestratorBadRequest("operation replay unavailable", status_code=409)
        assert physical["confirmed_observation_fence"] is not None
        assert request.fencing_epoch > physical["confirmed_observation_fence"]
        physical["containers"] = 0
        return _destroyed_cell(request)

    async def observe(_self, request):
        observations.append(request)
        assert request.fencing_epoch > destroys[0].fencing_epoch
        if lost_observation and len(observations) == 1:
            raise OrchestratorUnavailable("observation reply lost")
        physical["confirmed_observation_fence"] = request.fencing_epoch
        return replace(_destroyed_cell(request), state="partial")

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", destroy)
    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "observe_resources", observe)
    first = await client.delete(f"/api/projects/{project_id}")
    assert first.status_code == 503, first.text
    assert physical["containers"] == 1 and fake_teardown["repo"] == []
    second = await client.delete(f"/api/projects/{project_id}")
    if lost_observation:
        assert second.status_code == 503, second.text
        assert physical["containers"] == 1 and fake_teardown["repo"] == []
        second = await client.delete(f"/api/projects/{project_id}")
        assert len(observations) == 2
        assert observations[1].fencing_epoch > observations[0].fencing_epoch
    assert second.status_code == 204, second.text
    assert physical["containers"] == 0
    assert destroys[0].operation_id == destroys[1].operation_id
    assert destroys[-1].operation_id != destroys[0].operation_id
    assert await db_session.get(Project, project_id) is None


@pytest.mark.parametrize("wake_state", ["pending", "waiting_capacity"])
async def test_delete_cancels_undispatched_owner_wake_without_allocating_cpu(
    client, db_session, as_user, fake_teardown, monkeypatch, wake_state,
):
    from omnia_api.services.orchestrator_client import HttpProjectCellOrchestratorClient
    from omnia_api.services.project_cells import reserve_cell_operation

    owner, project, workspace = await _cell_project(db_session)
    operation, _ = await reserve_cell_operation(
        db_session, workspace_id=workspace.id, generation_run_id=None,
        kind="wake", idempotency_key="owner-preview:wake-before-delete", request={},
    )
    operation.status = wake_state
    await db_session.commit()
    as_user(owner)
    calls = []

    async def control(_self, request):
        calls.append(request.kind)
        assert request.kind == "destroy"
        return _destroyed_cell(request)

    monkeypatch.setattr(HttpProjectCellOrchestratorClient, "control", control)
    response = await client.delete(f"/api/projects/{project.id}")
    assert response.status_code == 204, response.text
    assert calls == ["destroy"]
