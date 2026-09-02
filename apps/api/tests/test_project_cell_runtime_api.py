from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services import orchestrator_client as oc
from omnia_api.services import project_cell_runtime as runtime


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


def _deny_legacy(monkeypatch):
    async def denied(*_args, **_kwargs):
        raise AssertionError("cell request reached legacy runtime")

    for name in ("get_status", "start", "stop", "set_keep_alive", "hot_reload",
                 "get_logs", "create_max_preview_session", "get_deploy"):
        if hasattr(oc, name):
            monkeypatch.setattr(oc, name, denied)


async def test_durable_cell_public_flow_survives_disabled_owner_flag(
    client, db_session, monkeypatch,
):
    _, project, run, workspace = await _seed(db_session, monkeypatch)
    _deny_legacy(monkeypatch)
    request = AsyncMock(return_value=_resources(workspace))
    monkeypatch.setattr(oc, "_request", request)
    bootstrap = f"{_origin(workspace.id)}/api/omnia/preview-session"
    bootstrap += "?expires=1893456000&signature=" + "a" * 43
    preview = AsyncMock(return_value=oc.ProjectCellPreviewSession(
        workspace_id=workspace.id, preview_url=_origin(workspace.id),
        bootstrap_url=bootstrap, expires_at="2030-01-01T00:00:00+00:00",
    ))
    monkeypatch.setattr(oc, "project_cell_create_preview_session", preview)
    base = f"/api/projects/{project.id}"
    for method, path in (("GET", "/runtime"), ("POST", "/runtime/start")):
        response = await client.request(method, base + path)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "running"
        assert response.json()["dev_url"] == _origin(workspace.id)
        assert response.json()["port"] is None
    response = await client.post(base + "/max/sync-kit")
    assert response.status_code == 200, response.text
    response = await client.post(base + "/max/preview-session")
    assert response.status_code == 200, response.text
    assert response.json()["url"] == bootstrap
    assert response.headers["cache-control"] == "no-store"
    preview.assert_awaited_once_with(
        workspace.id, generation_run_id=run.id, fencing_epoch=7,
    )
    assert all(call.args[1] == f"/internal/workspaces/{workspace.id}/resources"
               for call in request.await_args_list)


@pytest.mark.parametrize("active", [False, True])
async def test_selected_owner_without_workspace_never_starts_legacy(
    client, db_session, monkeypatch, active,
):
    _, project, _, _ = await _seed(
        db_session, monkeypatch, cell=False, enabled=True, active=active,
    )
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
    config = (await client.get(base + "/max/config")).json()["config"]
    response = await client.put(base + "/max/config", json=config)
    assert response.status_code == 409, response.text
    await db_session.refresh(project)
    assert project.current_snapshot_id == before


async def test_non_owner_cannot_access_cell_preview(client, db_session, monkeypatch):
    _, project, _, _ = await _seed(db_session, monkeypatch)
    other = User(email="other-cell-owner@example.com", password_hash="x")
    db_session.add(other)
    await db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: other
    _deny_legacy(monkeypatch)
    for method, path in (("GET", "/runtime"), ("POST", "/runtime/start"),
                         ("POST", "/max/preview-session")):
        response = await client.request(method, f"/api/projects/{project.id}" + path)
        assert response.status_code in {403, 404}


async def test_unselected_project_keeps_legacy_runtime(client, db_session, monkeypatch):
    _, project, _, _ = await _seed(db_session, monkeypatch, cell=False)
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


@pytest.mark.parametrize("cell", [False, True])
async def test_dark_cell_has_no_public_source_redirect_or_anonymous_fork(
    client, db_session, monkeypatch, cell,
):
    _, project, _, _ = await _seed(db_session, monkeypatch, cell=cell, enabled=not cell)
    _deny_legacy(monkeypatch)
    for path in (f"/p/{project.slug}", f"/p/{project.slug}?inspect=1",
                 f"/p/{project.slug}/src/app/page.tsx", f"/p/{project.slug}/remix"):
        response = await client.get(path)
        assert response.status_code == 404, (path, response.text)
    response = await client.post(f"/api/projects/{project.id}/fork")
    assert response.status_code == 404, response.text
    assert "set-cookie" not in response.headers
