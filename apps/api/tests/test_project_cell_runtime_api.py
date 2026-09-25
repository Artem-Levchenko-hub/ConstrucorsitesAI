from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.deps import get_current_user
from yleum_api.main import app
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.services import orchestrator_client as oc
from yleum_api.services import project_cell_runtime as runtime
from yleum_api.services.project_cells import (
    claim_cell_operation_committed,
    recover_interrupted_cell_operations,
)

# How long a test waits for a background task to reach a checkpoint. The
# assertion is that the checkpoint happens at all, not that it is fast: a
# one-second budget failed the release gate on a slow CI runner.
_EVENT_WAIT_SECONDS = 10


async def _seed(session, monkeypatch, *, cell=True, enabled=False, active=False):
    owner = User(
        email="cell-preview-owner@example.com", password_hash="x",
        email_verified_at=datetime.now(UTC), status="active", is_anon=False,
    )
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id, name="Cell preview", slug=f"cell-{uuid4().hex}",
        template="max_miniapp",
    )
    session.add(project)
    await session.flush()
    run = GenerationRun(
        project_id=project.id, user_id=owner.id, idempotency_key=uuid4().hex,
        status="running" if active else "completed", prompt_hash="a" * 64,
    )
    snapshot = Snapshot(project_id=project.id, commit_sha="a" * 40, prompt_text="build")
    session.add_all([run, snapshot])
    await session.flush()
    project.current_snapshot_id = snapshot.id
    workspace = None
    if cell:
        workspace = ProjectCellWorkspace(
            project_id=project.id, owner_id=owner.id, provider="docker_owner_canary",
            state="ready", generation_run_id=run.id, fencing_epoch=7,
        )
        session.add(workspace)
    await session.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "project_cell_docker_canary_enabled", enabled)
    monkeypatch.setattr(settings, "project_cell_canary_emails", owner.email)
    app.dependency_overrides[get_current_user] = lambda: owner
    return owner, project, run, workspace


def _resources(workspace, *, running=True):
    return oc.ProjectCellResourceResponse(
        workspace_id=workspace.id, state="resources_ready", provider_ref=None,
        fencing_epoch=7, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
        has_postgres=True, has_redis=True, has_draft_runtime=running,
        draft_state="running" if running else None, preview_url=_origin(workspace.id),
    ).to_wire_json()


def _origin(workspace_id):
    return f"https://cell-{workspace_id.hex[:12]}-dev.{get_settings().project_cell_preview_host_suffix}"


def _preview_session(workspace_id):
    origin = _origin(workspace_id)
    return oc.ProjectCellPreviewSession(
        workspace_id=workspace_id,
        preview_url=origin,
        bootstrap_url=(
            f"{origin}/api/omnia/preview-session?expires=1893456000&signature=" + "a" * 43
        ),
        expires_at="2030-01-01T00:00:00+00:00",
    )


def _deny_legacy(monkeypatch):
    async def denied(*_args, **_kwargs):
        raise AssertionError("cell request reached legacy runtime")

    for name in ("get_status", "start", "stop", "set_keep_alive", "hot_reload",
                 "get_logs", "create_max_preview_session", "get_deploy"):
        if hasattr(oc, name):
            monkeypatch.setattr(oc, name, denied)


@pytest.mark.parametrize("released", [False, True])
async def test_durable_cell_public_flow_survives_disabled_owner_flag(
    client, db_session, monkeypatch, released,
):
    owner, project, _run, workspace = await _seed(db_session, monkeypatch)
    if released:
        workspace.generation_run_id = None
        await db_session.commit()
    _deny_legacy(monkeypatch)
    request = AsyncMock(return_value=_resources(workspace))
    monkeypatch.setattr(oc, "_request", request)
    bootstrap = f"{_origin(workspace.id)}/api/omnia/preview-session"
    bootstrap += "?expires=1893456000&signature=" + "a" * 43
    preview = AsyncMock(return_value=oc.ProjectCellPreviewSession(
        workspace_id=workspace.id, preview_url=_origin(workspace.id),
        bootstrap_url=bootstrap, expires_at="2030-01-01T00:00:00+00:00",
    ))
    monkeypatch.setattr(oc, "project_cell_create_owner_preview_session", preview)
    agent_preview = AsyncMock(side_effect=AssertionError("owner preview used the agent lease"))
    monkeypatch.setattr(oc, "project_cell_create_preview_session", agent_preview)
    base = f"/api/projects/{project.id}"
    for method, path in (("GET", "/runtime"), ("POST", "/runtime/start")):
        response = await client.request(method, base + path)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "running"
        assert response.json()["dev_url"] == _origin(workspace.id)
        assert response.json()["port"] is None
    response = await client.post(base + "/max/sync-kit")
    assert response.status_code == 200, response.text
    assert response.json()["application_mode"] == "runtime"
    assert response.json()["synced_snapshot_id"] is None
    response = await client.post(base + "/max/preview-session")
    assert response.status_code == 200, response.text
    assert response.json()["url"] == bootstrap
    assert response.headers["cache-control"] == "no-store"
    preview.assert_awaited_once_with(
        workspace.id, project_id=project.id, owner_id=owner.id,
    )
    agent_preview.assert_not_awaited()
    if released:
        await db_session.refresh(workspace)
        assert workspace.generation_run_id is None
        assert workspace.fencing_epoch == 7
    assert all(call.args[1] == f"/internal/workspaces/{workspace.id}/resources"
               for call in request.await_args_list)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("admitted", [False, True])
async def test_selected_owner_without_workspace_never_starts_legacy(
    client, db_session, monkeypatch, active, admitted,
):
    _, project, _, _ = await _seed(
        db_session, monkeypatch, cell=False, enabled=not admitted, active=active,
    )
    if admitted:
        project.project_cell_enabled = True
        await db_session.commit()
        await db_session.refresh(project)
        assert project.project_cell_enabled is True
        monkeypatch.setattr(get_settings(), "project_cell_general_availability_enabled", False)
    _deny_legacy(monkeypatch)
    base = f"/api/projects/{project.id}"
    status = await client.get(base + "/runtime")
    assert status.status_code == 200
    assert status.json()["state"] == ("provisioning" if active else "stopped")
    start = await client.post(base + "/runtime/start")
    assert start.status_code == (200 if active else 409)
    assert (await client.post(base + "/max/preview-session")).status_code == 409


async def test_unsupported_cell_actions_fail_closed(client, db_session, monkeypatch):
    _, project, _, _ = await _seed(db_session, monkeypatch)
    _deny_legacy(monkeypatch)
    before = project.current_snapshot_id
    base = f"/api/projects/{project.id}"
    for method, path, body in (
        ("GET", "/runtime/logs", None), ("POST", "/runtime/stop", {}),
        ("POST", "/runtime/keep-alive", {"enabled": True}),
        ("POST", "/deploy", {}),
    ):
        response = await client.request(method, base + path, json=body)
        assert response.status_code == 409, (path, response.text)
    await db_session.refresh(project)
    assert project.current_snapshot_id == before


@pytest.mark.parametrize("first_apply_fails", [False, True])
async def test_cell_config_save_applies_without_generation_or_source_changes(
    client, db_session, monkeypatch, first_apply_fails,
):
    from yleum_api.models.max_project_config import MaxProjectConfig

    _, project, run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    await db_session.commit()
    _deny_legacy(monkeypatch)
    monkeypatch.setattr(oc, "_request", AsyncMock(return_value=_resources(workspace)))
    apply = AsyncMock(return_value=True)
    if first_apply_fails:
        apply.side_effect = [oc.OrchestratorUnavailable("test outage"), True, True]
    monkeypatch.setattr(oc, "project_cell_apply_business_config", apply)
    before = project.current_snapshot_id
    base = f"/api/projects/{project.id}/max/config"
    config = (await client.get(base)).json()["config"]
    config["operator"]["legal_name"] = "Example owner"
    config["support"]["response_time"] = "Within one working day"
    first = await client.put(base, json=config)
    assert first.status_code == (503 if first_apply_fails else 200), first.text
    saved = await client.get(base)
    assert saved.json()["config"] == config
    assert saved.json()["application_mode"] == "runtime"
    assert saved.json()["config_version"] == 1
    if first_apply_fails:
        assert "Данные сохранены" in first.json()["error"]["message"]
        assert saved.json()["synced_snapshot_id"] is None
    for _ in range(2):
        response = await client.put(base, json=config)
        assert response.status_code == 200, response.text
        assert response.json()["config_version"] == 1
        assert response.json()["application_mode"] == "runtime"
    record = await db_session.get(MaxProjectConfig, project.id, populate_existing=True)
    assert record.synced_snapshot_id == before
    await db_session.refresh(workspace)
    await db_session.refresh(project)
    await db_session.refresh(run)
    assert workspace.generation_run_id is None and workspace.fencing_epoch == 7
    assert project.current_snapshot_id == before
    assert run.status == "completed"
    for call in apply.await_args_list:
        assert call.kwargs["version"] == 1
        assert "max_url_attached" not in call.kwargs["config"]


async def test_cell_config_does_not_mutate_during_generation(client, db_session, monkeypatch):
    from yleum_api.models.max_project_config import MaxProjectConfig

    _, project, _, _ = await _seed(db_session, monkeypatch, active=True)
    base = f"/api/projects/{project.id}/max/config"
    config = (await client.get(base)).json()["config"]
    assert (await client.put(base, json=config)).status_code == 409
    assert await db_session.get(MaxProjectConfig, project.id) is None


async def test_cell_config_saves_while_asleep_without_cpu_admission(
    client, db_session, monkeypatch,
):
    _, project, _, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    workspace.state = "stopped"
    await db_session.commit()
    wake = AsyncMock(side_effect=AssertionError("metadata save must not wake resources"))
    monkeypatch.setattr(runtime, "start_project_cell_runtime", wake)
    publish = AsyncMock(return_value={"applied": True})
    apply = AsyncMock(return_value=False)
    monkeypatch.setattr(oc, "configure_published_cell", publish)
    monkeypatch.setattr(oc, "project_cell_apply_business_config", apply)
    base = f"/api/projects/{project.id}/max/config"
    config = (await client.get(base)).json()["config"]
    config["app_name"] = "Saved without waking"
    for _ in range(2):
        response = await client.put(base, json=config)
        assert response.status_code == 200, response.text
        assert response.json()["config"] == config
        assert response.json()["config_version"] == 1
        assert response.json()["synced_snapshot_id"] is None
    wake.assert_not_awaited()
    assert apply.await_count == 2 and publish.await_count == 2
    await db_session.refresh(workspace)
    assert workspace.state == "stopped" and workspace.generation_run_id is None


@pytest.mark.parametrize("admitted", [False, True])
async def test_non_owner_cannot_access_cell_preview(client, db_session, monkeypatch, admitted):
    _, project, _, _ = await _seed(db_session, monkeypatch, cell=not admitted)
    if admitted:
        project.project_cell_enabled = True
    other = User(email="other-cell-owner@example.com", password_hash="x")
    db_session.add(other)
    await db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: other
    _deny_legacy(monkeypatch)
    for method, path in (("GET", "/runtime"), ("POST", "/runtime/start"),
                         ("POST", "/max/preview-session")):
        response = await client.request(method, f"/api/projects/{project.id}" + path)
        assert response.status_code in {403, 404}


async def test_competing_preview_start_returns_retryable_busy_without_waiting(
    client, db_session, monkeypatch,
):
    _, project, _, workspace = await _seed(db_session, monkeypatch)
    _deny_legacy(monkeypatch)
    request = AsyncMock(side_effect=AssertionError("busy start must not call controller"))
    monkeypatch.setattr(oc, "_request", request)
    async with db_session.bind.connect() as blocker:
        await blocker.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
            {"project_id": str(project.id)},
        )
        response = await asyncio.wait_for(
            client.post(f"/api/projects/{project.id}/runtime/start"), timeout=2,
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "orchestrator_unavailable"
    request.assert_not_awaited()
    await db_session.refresh(workspace)
    assert workspace.fencing_epoch == 7


async def test_unselected_project_keeps_legacy_runtime(client, db_session, monkeypatch):
    _, project, _, _ = await _seed(db_session, monkeypatch, cell=False)
    monkeypatch.setattr(get_settings(), "project_cell_general_availability_enabled", True)
    assert project.project_cell_enabled is False
    legacy = AsyncMock(return_value={"state": "stopped", "keep_alive": False})
    monkeypatch.setattr(oc, "get_status", legacy)
    response = await client.get(f"/api/projects/{project.id}/runtime")
    assert response.status_code == 200
    legacy.assert_awaited_once_with(project.id)


async def test_start_uses_fenced_cell_snapshot_under_project_lock(
    client, db_session, test_engine, monkeypatch,
):
    _, project, run, workspace = await _seed(db_session, monkeypatch)
    _deny_legacy(monkeypatch)
    monkeypatch.setattr(
        oc, "_request", AsyncMock(return_value=_resources(workspace, running=False)),
    )

    async def bootstrap(workspace_id, **kwargs):
        assert workspace_id == workspace.id
        assert kwargs == {"generation_run_id": run.id, "fencing_epoch": 7}
        async with async_sessionmaker(test_engine)() as competing:
            acquired = await competing.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:project_id))"),
                {"project_id": str(project.id)},
            )
            assert acquired is False
        return SimpleNamespace(
            generation_run_id=run.id, fencing_epoch=7, workspace_revision="b" * 64,
        )

    apply = AsyncMock(return_value=oc.ProjectCellDraftApplyResponse(
        workspace_revision="b" * 64, preview_url=_origin(workspace.id), migration_exit_code=0,
    ))
    monkeypatch.setattr(oc, "project_cell_agent_bootstrap", bootstrap)
    monkeypatch.setattr(oc, "project_cell_apply_draft", apply)
    response = await client.post(f"/api/projects/{project.id}/runtime/start")
    assert response.status_code == 200, response.text
    apply.assert_awaited_once_with(
        workspace.id, generation_run_id=run.id, fencing_epoch=7,
        expected_revision="b" * 64, files={}, deletes=(),
    )


async def test_resource_identity_mismatch_is_rejected(monkeypatch):
    workspace = SimpleNamespace(id=uuid4())
    payload = _resources(workspace)
    monkeypatch.setattr(oc, "_request", AsyncMock(return_value=payload))
    with pytest.raises(oc.OrchestratorUnavailable, match="invalid Project Cell resource"):
        await runtime._get_cell_resources(uuid4())


async def test_released_owner_can_restart_preview_without_agent_bootstrap(
    client, db_session, monkeypatch,
):
    owner, project, _run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    await db_session.commit()
    _deny_legacy(monkeypatch)
    monkeypatch.setattr(
        oc, "_request", AsyncMock(return_value=_resources(workspace, running=False)),
    )
    start = AsyncMock(return_value=SimpleNamespace(preview_url=_origin(workspace.id)))
    bootstrap = AsyncMock(side_effect=AssertionError("owner restart must not bootstrap source"))
    monkeypatch.setattr(oc, "project_cell_start_owner_preview", start)
    monkeypatch.setattr(oc, "project_cell_agent_bootstrap", bootstrap)
    response = await client.post(f"/api/projects/{project.id}/runtime/start")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "running"
    start.assert_awaited_once_with(workspace.id, project_id=project.id, owner_id=owner.id)
    bootstrap.assert_not_awaited()
    await db_session.refresh(workspace)
    assert workspace.generation_run_id is None


@pytest.mark.parametrize("paused_state", ["resources_paused", "retained"])
@pytest.mark.parametrize("lost_wake_response", [False, True])
async def test_owner_start_wakes_paused_cell_with_durable_retry_and_no_agent_lease(
    client, db_session, test_engine, monkeypatch, paused_state, lost_wake_response,
):
    from sqlalchemy import select

    from yleum_api.models.project_cell import ProjectCellOperation

    _owner, project, _run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    workspace.state = "stopped"
    await db_session.commit()
    snapshot_id = project.current_snapshot_id
    woke = False
    expected_fence = 8
    wake_envelopes = []

    async def request(method, path, **kwargs):
        nonlocal woke
        if path.endswith("/control"):
            payload = kwargs["json"]
            assert payload["kind"] == "wake"
            assert payload["fencing_epoch"] == expected_fence
            wake_envelopes.append(payload)
            woke = True
            if lost_wake_response and len(wake_envelopes) == 1:
                raise oc.OrchestratorUnavailable("wake response lost after effect")
        result = _resources(workspace, running=woke and lost_wake_response)
        result.update(state="resources_ready" if woke else paused_state,
                      fencing_epoch=expected_fence if woke else expected_fence - 1)
        return result

    async def owner_start(*args, **kwargs):
        assert woke, "owner-start reached a paused bundle without waking it"
        return SimpleNamespace(preview_url=_origin(workspace.id))

    _deny_legacy(monkeypatch)
    monkeypatch.setattr(oc, "_request", request)
    monkeypatch.setattr(oc, "project_cell_start_owner_preview", owner_start)
    bootstrap = AsyncMock(side_effect=AssertionError("owner wake must preserve generated source"))
    monkeypatch.setattr(oc, "project_cell_agent_bootstrap", bootstrap)
    if lost_wake_response:
        first = await client.post(f"/api/projects/{project.id}/runtime/start")
        assert first.status_code == 200, first.text
        assert first.json()["state"] == "provisioning"
        repeated = await client.post(f"/api/projects/{project.id}/runtime/start")
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["state"] == "provisioning"
        assert len(wake_envelopes) == 1
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        assert await runtime.advance_owner_wake_operations(
            factory, oc.HttpProjectCellOrchestratorClient()
        ) == 1
    response = await client.post(f"/api/projects/{project.id}/runtime/start")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "running"
    assert len(wake_envelopes) == (2 if lost_wake_response else 1)
    assert all(envelope == wake_envelopes[0] for envelope in wake_envelopes)
    await db_session.refresh(workspace)
    await db_session.refresh(project)
    assert workspace.generation_run_id is None
    assert workspace.fencing_epoch == 8
    assert workspace.state == "ready"
    assert project.current_snapshot_id == snapshot_id
    operations = (await db_session.scalars(select(ProjectCellOperation).where(
        ProjectCellOperation.workspace_id == workspace.id,
    ))).all()
    assert len(operations) == 1
    assert operations[0].kind == "wake"
    assert operations[0].status == "completed"
    assert operations[0].generation_run_id is None
    bootstrap.assert_not_awaited()

    # A later capacity pause advances the durable fence. Reopening must not
    # replay the completed wake from the previous pause/wake cycle.
    workspace.fencing_epoch = 9
    workspace.state = "stopped"
    await db_session.commit()
    woke = False
    expected_fence = 10
    previous_count = len(wake_envelopes)
    response = await client.post(f"/api/projects/{project.id}/runtime/start")
    assert response.status_code == 200, response.text
    assert len(wake_envelopes) == previous_count + 1
    assert wake_envelopes[-1]["operation_id"] != wake_envelopes[0]["operation_id"]
    await db_session.refresh(workspace)
    assert workspace.fencing_epoch == 10
    assert workspace.generation_run_id is None


async def test_owner_capacity_wait_is_visible_without_polling_redispatch(
    client, db_session, monkeypatch,
):
    _owner, project, _run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    workspace.state = "stopped"
    await db_session.commit()
    _deny_legacy(monkeypatch)
    control_calls = 0

    async def request(method, path, **kwargs):
        nonlocal control_calls
        if method == "GET":
            payload = _resources(workspace, running=False)
            payload["state"] = "resources_paused"
            return payload
        assert method == "POST" and path.endswith("/control")
        control_calls += 1
        payload = kwargs["json"]
        raise oc.ProjectCellCapacityWait(
            oc.ProjectCellCapacityRejection(
                operation_id=UUID(payload["operation_id"]),
                fencing_epoch=payload["fencing_epoch"],
                request_digest=payload["request_digest"],
                effect_applied=False,
                reason="insufficient_cpu",
                retry_after_seconds=1,
            )
        )

    monkeypatch.setattr(oc, "_request", request)
    url = f"/api/projects/{project.id}/runtime"

    first = await client.post(url + "/start")
    second = await client.post(url + "/start")
    status = await client.get(url)

    assert first.status_code == second.status_code == status.status_code == 200
    assert first.json()["state"] == "provisioning"
    assert second.json()["state"] == "provisioning"
    assert status.json()["state"] == "provisioning"
    assert control_calls == 1
    operations = list(
        (
            await db_session.scalars(
                select(ProjectCellOperation).where(
                    ProjectCellOperation.workspace_id == workspace.id
                )
            )
        ).all()
    )
    assert len(operations) == 1
    assert operations[0].status == "waiting_capacity"
    assert operations[0].attempt_count == 1


async def test_owner_capacity_consumer_completes_once_after_capacity_release(
    client, db_session, test_engine, monkeypatch,
):
    owner, project, _run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    workspace.state = "stopped"
    await db_session.commit()
    _deny_legacy(monkeypatch)
    first_dispatch = True

    async def request(method, path, **kwargs):
        nonlocal first_dispatch
        if method == "GET":
            payload = _resources(workspace, running=False)
            payload["state"] = "resources_paused"
            return payload
        payload = kwargs["json"]
        assert first_dispatch
        first_dispatch = False
        raise oc.ProjectCellCapacityWait(
            oc.ProjectCellCapacityRejection(
                operation_id=UUID(payload["operation_id"]),
                fencing_epoch=payload["fencing_epoch"],
                request_digest=payload["request_digest"],
                effect_applied=False,
                reason="insufficient_cpu",
                retry_after_seconds=1,
            )
        )

    monkeypatch.setattr(oc, "_request", request)
    response = await client.post(f"/api/projects/{project.id}/runtime/start")
    assert response.status_code == 200
    assert response.json()["state"] == "provisioning"

    operation = await db_session.scalar(
        select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace.id
        )
    )
    assert operation is not None
    operation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    dispatched: list[object] = []

    async def control(request):
        dispatched.append(request)
        return oc.ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref=f"docker-owner-canary:{workspace.id}",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
            has_draft_runtime=True,
            draft_state=None,
            preview_url=None,
        )

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    worker_client = SimpleNamespace(control=control)
    start_preview = AsyncMock(return_value=_preview_session(workspace.id))
    monkeypatch.setattr(oc, "project_cell_start_owner_preview", start_preview)
    assert await runtime.advance_owner_wake_operations(factory, worker_client) == 1
    assert await runtime.advance_owner_wake_operations(factory, worker_client) == 0

    await db_session.refresh(operation)
    await db_session.refresh(workspace)
    assert operation.status == "completed"
    assert operation.attempt_count == 2
    assert workspace.state == "ready"
    assert workspace.generation_run_id is None
    assert len(dispatched) == 1
    envelope = dispatched[0]
    assert envelope.operation_id == operation.id
    assert envelope.fencing_epoch == operation.fencing_epoch
    assert envelope.request_digest == operation.request_digest
    assert owner.id == workspace.owner_id
    start_preview.assert_awaited_once_with(
        workspace.id, project_id=project.id, owner_id=owner.id
    )


@pytest.mark.parametrize("claim_failure", ["lost_response", "cancelled"])
async def test_owner_preview_claim_unknown_commit_replays_same_envelope(
    client, db_session, test_engine, monkeypatch, claim_failure,
):
    owner, project, _run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    workspace.state = "stopped"
    await db_session.commit()
    _deny_legacy(monkeypatch)

    async def request(method, path, **kwargs):
        if method == "GET":
            payload = _resources(workspace, running=False)
            payload["state"] = "resources_paused"
            return payload
        payload = kwargs["json"]
        raise oc.ProjectCellCapacityWait(
            oc.ProjectCellCapacityRejection(
                operation_id=UUID(payload["operation_id"]),
                fencing_epoch=payload["fencing_epoch"],
                request_digest=payload["request_digest"],
                effect_applied=False,
                reason="insufficient_cpu",
                retry_after_seconds=1,
            )
        )

    monkeypatch.setattr(oc, "_request", request)
    response = await client.post(f"/api/projects/{project.id}/runtime/start")
    assert response.status_code == 200
    operation = await db_session.scalar(
        select(ProjectCellOperation).where(ProjectCellOperation.workspace_id == workspace.id)
    )
    assert operation is not None
    operation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    replayed = []

    async def control(request):
        replayed.append(request)
        return oc.ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref=f"docker-owner-canary:{workspace.id}",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
            has_draft_runtime=True,
            draft_state=None,
            preview_url=None,
        )

    begin = runtime._begin_owner_preview_continuation
    claim_attempts = 0
    claim_committed = asyncio.Event()
    release_claim_receipt = asyncio.Event()

    async def uncertain_begin(*args, **kwargs):
        nonlocal claim_attempts
        continuation = await begin(*args, **kwargs)
        claim_attempts += 1
        if claim_attempts == 1:
            if claim_failure == "cancelled":
                claim_committed.set()
                await release_claim_receipt.wait()
                return continuation
            raise ConnectionError("owner preview claim commit response lost")
        return continuation

    monkeypatch.setattr(runtime, "_begin_owner_preview_continuation", uncertain_begin)
    start_preview = AsyncMock(return_value=_preview_session(workspace.id))
    monkeypatch.setattr(oc, "project_cell_start_owner_preview", start_preview)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    worker_client = SimpleNamespace(control=control)

    if claim_failure == "cancelled":
        advance = asyncio.create_task(
            runtime.advance_owner_wake_operations(factory, worker_client)
        )
        await asyncio.wait_for(claim_committed.wait(), timeout=_EVENT_WAIT_SECONDS)
        advance.cancel()
        await asyncio.sleep(0)
        release_claim_receipt.set()
        with pytest.raises(asyncio.CancelledError):
            await advance
    else:
        assert await runtime.advance_owner_wake_operations(factory, worker_client) == 0
    await db_session.refresh(operation)
    assert operation.status == "indeterminate"
    assert operation.result_payload["owner_preview_continuation"] == "indeterminate"
    start_preview.assert_not_awaited()

    assert await runtime.advance_owner_wake_operations(factory, worker_client) == 1
    await db_session.refresh(operation)
    assert operation.status == "completed"
    assert operation.result_payload["owner_preview_continuation"] == "completed"
    assert claim_attempts == 2
    start_preview.assert_awaited_once_with(
        workspace.id, project_id=project.id, owner_id=owner.id
    )
    assert len(replayed) == 2
    assert {request.operation_id for request in replayed} == {operation.id}
    assert {request.fencing_epoch for request in replayed} == {operation.fencing_epoch}
    assert {request.request_digest for request in replayed} == {operation.request_digest}


async def test_owner_capacity_unknown_start_and_finalization_failures_replay_same_envelope(
    client, db_session, test_engine, monkeypatch,
):
    owner, project, _run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    workspace.state = "stopped"
    await db_session.commit()
    _deny_legacy(monkeypatch)

    async def request(method, path, **kwargs):
        if method == "GET":
            payload = _resources(workspace, running=False)
            payload["state"] = "resources_paused"
            return payload
        payload = kwargs["json"]
        raise oc.ProjectCellCapacityWait(
            oc.ProjectCellCapacityRejection(
                operation_id=UUID(payload["operation_id"]),
                fencing_epoch=payload["fencing_epoch"],
                request_digest=payload["request_digest"],
                effect_applied=False,
                reason="insufficient_cpu",
                retry_after_seconds=1,
            )
        )

    monkeypatch.setattr(oc, "_request", request)
    response = await client.post(f"/api/projects/{project.id}/runtime/start")
    assert response.status_code == 200
    operation = await db_session.scalar(
        select(ProjectCellOperation).where(ProjectCellOperation.workspace_id == workspace.id)
    )
    assert operation is not None
    operation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    replayed = []

    async def control(request):
        replayed.append(request)
        return oc.ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref=f"docker-owner-canary:{workspace.id}",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
            has_draft_runtime=True,
            draft_state=None,
            preview_url=None,
        )

    start_preview = AsyncMock(
        side_effect=[
            oc.OrchestratorUnavailable("owner preview response lost"),
            _preview_session(workspace.id),
            _preview_session(workspace.id),
            _preview_session(workspace.id),
        ]
    )
    monkeypatch.setattr(oc, "project_cell_start_owner_preview", start_preview)
    finish = runtime._finish_owner_preview_continuation
    finish_calls = 0

    async def flaky_finish(*args, **kwargs):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:
            raise ConnectionError("SQL finalization response lost")
        if finish_calls == 2:
            raise asyncio.CancelledError
        return await finish(*args, **kwargs)

    monkeypatch.setattr(runtime, "_finish_owner_preview_continuation", flaky_finish)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    worker_client = SimpleNamespace(control=control)

    assert await runtime.advance_owner_wake_operations(factory, worker_client) == 0
    await db_session.refresh(operation)
    assert operation.status == "indeterminate"
    assert await runtime.advance_owner_wake_operations(factory, worker_client) == 0
    await db_session.refresh(operation)
    assert operation.status == "indeterminate"
    with pytest.raises(asyncio.CancelledError):
        await runtime.advance_owner_wake_operations(factory, worker_client)
    await db_session.refresh(operation)
    assert operation.status == "indeterminate"
    assert await runtime.advance_owner_wake_operations(factory, worker_client) == 1
    await db_session.refresh(operation)
    assert operation.status == "completed"
    assert start_preview.await_count == 4
    assert finish_calls == 3
    assert len(replayed) == 4
    assert {request.operation_id for request in replayed} == {operation.id}
    assert {request.fencing_epoch for request in replayed} == {operation.fencing_epoch}
    assert {request.request_digest for request in replayed} == {operation.request_digest}
    assert all(
        call.kwargs == {"project_id": project.id, "owner_id": owner.id}
        for call in start_preview.await_args_list
    )


async def test_owner_capacity_consumer_replays_same_envelope_after_restart(
    client, db_session, test_engine, monkeypatch,
):
    _owner, project, _run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    workspace.state = "stopped"
    await db_session.commit()
    _deny_legacy(monkeypatch)

    async def request(method, path, **kwargs):
        if method == "GET":
            payload = _resources(workspace, running=False)
            payload["state"] = "resources_paused"
            return payload
        payload = kwargs["json"]
        raise oc.ProjectCellCapacityWait(
            oc.ProjectCellCapacityRejection(
                operation_id=UUID(payload["operation_id"]),
                fencing_epoch=payload["fencing_epoch"],
                request_digest=payload["request_digest"],
                effect_applied=False,
                reason="insufficient_cpu",
                retry_after_seconds=1,
            )
        )

    monkeypatch.setattr(oc, "_request", request)
    assert (
        await client.post(f"/api/projects/{project.id}/runtime/start")
    ).json()["state"] == "provisioning"
    operation = await db_session.scalar(
        select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace.id
        )
    )
    assert operation is not None
    operation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    claimed = await claim_cell_operation_committed(factory, operation.id)
    assert claimed.operation_id == operation.id
    assert claimed.fencing_epoch == 9
    async with factory() as recovery_session:
        assert await recover_interrupted_cell_operations(recovery_session) == 1
        await recovery_session.commit()

    replayed: list[object] = []

    async def control(request):
        replayed.append(request)
        return oc.ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref=f"docker-owner-canary:{workspace.id}",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
            has_draft_runtime=True,
            draft_state="running",
            preview_url=_origin(workspace.id),
        )

    assert await runtime.advance_owner_wake_operations(
        factory, SimpleNamespace(control=control)
    ) == 1
    await db_session.refresh(operation)
    assert operation.status == "completed"
    assert operation.attempt_count == 3
    assert len(replayed) == 1
    assert replayed[0].operation_id == operation.id
    assert replayed[0].request_digest == operation.request_digest
    await db_session.refresh(workspace)
    assert workspace.fencing_epoch == 9
    assert workspace.state == "ready"
    assert workspace.generation_run_id is None
