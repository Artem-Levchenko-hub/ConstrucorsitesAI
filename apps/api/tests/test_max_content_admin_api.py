"""Real owner endpoints and DB CAS; runtime clients remain isolated fake transports."""

from unittest.mock import AsyncMock

import pytest

from tests.test_project_cell_runtime_api import _seed
from yleum_api.core.deps import get_current_user
from yleum_api.main import app
from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.models.user import User
from yleum_api.services import orchestrator_client as oc


async def seed_catalog(db_session, monkeypatch):
    owner, project, run, workspace = await _seed(db_session, monkeypatch)
    workspace.generation_run_id = None
    await db_session.commit()
    monkeypatch.setattr(oc, "get_deploy", AsyncMock(return_value={"phase": "done"}))
    monkeypatch.setattr(oc, "configure_published_cell", AsyncMock(return_value={"applied": True}))
    monkeypatch.setattr(oc, "project_cell_apply_business_config", AsyncMock(return_value=False))

    async def public_status(_project, body):
        return {"state": "pending", "desired_config_version": body["desired_config_version"],
                "controller_config_version": body["desired_config_version"],
                "reason": "runtime_not_confirmed"}

    async def preview_status(_workspace, body):
        return {"state": "deferred", "desired_config_version": body["desired_config_version"],
                "reason": "preview_sleeping"}

    monkeypatch.setattr(oc, "published_cell_config_status", public_status)
    monkeypatch.setattr(oc, "project_cell_business_config_status", preview_status)
    return owner, project, run, workspace


async def test_two_tabs_keep_ids_and_unrelated_config_with_stale_equal_convergence(
    client, db_session, monkeypatch,
):
    _owner, project, run, workspace = await seed_catalog(db_session, monkeypatch)
    before = project.current_snapshot_id
    path = f"/api/projects/{project.id}/max/content"
    first = {"expected_config_version": 0, "content": [{"id": "tea", "title": "Tea"}]}
    saved = await client.patch(path, json=first)
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] == {"state": "saved", "config_version": 1}
    assert saved.json()["public"]["state"] == "pending"
    assert saved.json()["preview"]["state"] == "deferred"
    second = {"expected_config_version": 1, "content": [{"id": "tea", "title": "New tea"}]}
    assert (await client.patch(path, json=second)).status_code == 200
    conflict = await client.patch(path, json=first)
    assert conflict.status_code == 409
    second["expected_config_version"] = 0
    repeated = await client.patch(path, json=second)
    assert repeated.status_code == 200 and repeated.json()["config_version"] == 2
    fresh = await client.get(path)
    assert fresh.json()["content"][0]["id"] == "tea"
    assert fresh.json()["content"][0]["title"] == "New tea"
    assert fresh.headers["cache-control"] == "no-store"
    record = await db_session.get(MaxProjectConfig, project.id)
    assert record.config["summary"] == "Мини-приложение для пользователей MAX"
    await db_session.refresh(project)
    await db_session.refresh(run)
    await db_session.refresh(workspace)
    assert project.current_snapshot_id == before
    assert run.status == "completed" and workspace.fencing_epoch == 7
    assert workspace.generation_run_id is None


async def test_saved_survives_public_outage_and_legacy_put_requires_content_baseline(
    client, db_session, monkeypatch,
):
    _, project, _, _ = await seed_catalog(db_session, monkeypatch)
    monkeypatch.setattr(
        oc, "configure_published_cell", AsyncMock(side_effect=oc.OrchestratorUnavailable("outage")),
    )
    path = f"/api/projects/{project.id}/max/content"
    payload = {"expected_config_version": 0, "content": [{"id": "tea", "title": "Tea"}]}
    assert (await client.patch(path, json=payload)).status_code == 200
    fresh = await client.get(path)
    assert fresh.json()["config_version"] == 1 and fresh.json()["content"][0]["id"] == "tea"
    config_path = f"/api/projects/{project.id}/max/config"
    config = (await client.get(config_path)).json()["config"]
    config["content"] = []
    assert (await client.put(config_path, json=config)).status_code == 409
    stale = await client.put(config_path, json=config, headers={"X-Max-Config-Version": "0"})
    assert stale.status_code == 409
    assert (await client.get(path)).json()["config_version"] == 1


@pytest.mark.parametrize("principal", ["foreign", "anonymous", "unauthenticated"])
async def test_catalog_owner_boundary(client, db_session, monkeypatch, principal):
    owner, project, _, _ = await seed_catalog(db_session, monkeypatch)
    if principal == "foreign":
        other = User(email="catalog-foreign@example.com", password_hash="x", is_anon=False)
        db_session.add(other)
        await db_session.commit()
        app.dependency_overrides[get_current_user] = lambda: other
    elif principal == "anonymous":
        owner.is_anon = True
        await db_session.commit()
    else:
        app.dependency_overrides.pop(get_current_user)
    path = f"/api/projects/{project.id}/max/content"
    assert (await client.get(path)).status_code in {401, 403, 404}
    denied = await client.patch(path, json={"expected_config_version": 0, "content": []})
    assert denied.status_code in {401, 403, 404}
    assert await db_session.get(MaxProjectConfig, project.id) is None
