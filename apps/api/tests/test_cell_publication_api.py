from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.core.crypto import encrypt_strong
from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.user import User
from omnia_api.services import orchestrator_client, project_cell_runtime
from tests.test_cell_publication_evidence import evidence


async def seed(session):
    value = evidence()
    project, run = value["project"], value["run"]
    user = User(
        id=project.owner_id, email="publish-owner@example.com", password_hash="test",
        email_verified_at=datetime.now(UTC), status="active", is_anon=False,
    )
    session.add(user)
    await session.flush()
    snapshot_id = project.current_snapshot_id
    project.current_snapshot_id = None
    session.add(project)
    await session.flush()
    run.prompt_hash, run.idempotency_key = "0" * 64, uuid4().hex
    value["snapshot"].prompt_text = "Create a real app"
    session.add_all([run, value["snapshot"]])
    await session.flush()
    project.current_snapshot_id = snapshot_id
    session.add(value["workspace"])
    await session.flush()
    session.add_all([value["candidate"], value["proof"]])
    await session.flush()
    session.add_all(value["results"])
    integration = MaxIntegration(
        project_id=project.id, owner_id=user.id, status="verified",
        bot_token_enc=encrypt_strong("disposable-test-bot"),
        webhook_secret_enc=encrypt_strong("disposable-test-webhook"),
        verified_at=datetime.now(UTC),
    )
    session.add(integration)
    session.add(MaxProjectConfig(
        project_id=project.id, owner_id=user.id, config_version=2,
        config={
            "app_name": "Warehouse", "app_type": "custom", "summary": "Stock records",
            "primary_action": "Add stock", "operator": {"legal_name": "QA owner"},
            "support": {"email": "qa@example.com"}, "legal": {"terms_accepted": True},
        },
    ))
    await session.commit()
    app.dependency_overrides[get_current_user] = lambda: user
    return value, integration


async def test_publish_submits_only_the_current_accepted_cell(client, db_session, monkeypatch):
    value, _ = await seed(db_session)
    project = value["project"]
    submit = AsyncMock(return_value={"phase": "queued", "run_id": "public-one"})
    monkeypatch.setattr(orchestrator_client, "publish_project_cell", submit)
    monkeypatch.setattr(project_cell_runtime, "_get_cell_resources", AsyncMock(
        return_value=SimpleNamespace(state="resources_ready"),
    ))
    response = await client.post(
        f"/api/projects/{project.id}/deploy", json={"idempotency_key": "publish-once"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == "public-one"
    payload = submit.await_args.args[1]
    assert payload["candidate_id"] == str(value["candidate"].id)
    assert payload["snapshot_id"] == str(value["snapshot"].id)
    assert payload["fencing_epoch"] == 8
    assert payload["accepted_fencing_epoch"] == 7
    assert payload["runtime_env"]["MAX_BOT_TOKEN"] == "disposable-test-bot"
    assert "disposable-test" not in response.text


@pytest.mark.parametrize("interrupted", [False, True])
async def test_publish_wakes_or_replays_idle_source_then_checks_fresh_fence(
    client, db_session, monkeypatch, interrupted,
):
    value, _ = await seed(db_session)
    workspace = value["workspace"]
    workspace.state = "provisioning" if interrupted else "stopped"
    await db_session.commit()
    pending = SimpleNamespace(id=uuid4()) if interrupted else None
    monkeypatch.setattr(
        project_cell_runtime, "_unfinished_owner_wake", AsyncMock(return_value=pending),
    )
    ready = interrupted

    async def resources(_id):
        return SimpleNamespace(state="resources_ready" if ready else "retained")

    async def wake(session, current, *, operation):
        nonlocal ready
        assert operation is pending
        current.state, current.fencing_epoch = "ready", 9
        await session.commit()
        ready = True

    wake_mock = AsyncMock(side_effect=wake)
    monkeypatch.setattr(project_cell_runtime, "_get_cell_resources", resources)
    monkeypatch.setattr(project_cell_runtime, "_wake_owner_workspace", wake_mock)
    submit = AsyncMock(return_value={"phase": "queued", "run_id": "after-wake"})
    monkeypatch.setattr(orchestrator_client, "publish_project_cell", submit)
    response = await client.post(f'/api/projects/{value["project"].id}/deploy', json={})
    assert response.status_code == 200, response.text
    wake_mock.assert_awaited_once()
    payload = submit.await_args.args[1]
    assert payload["fencing_epoch"] == 9
    assert payload["accepted_fencing_epoch"] == 7
    assert workspace.generation_run_id is None


@pytest.mark.parametrize("reason", ["proof", "generation", "other_owner"])
async def test_publish_rejects_unproven_busy_or_other_owner(
    client, db_session, monkeypatch, reason,
):
    value, _ = await seed(db_session)
    if reason == "proof":
        value["results"][-1].outcome = "red"
    elif reason == "generation":
        value["run"].status = "running"
    else:
        app.dependency_overrides[get_current_user] = lambda: User(id=uuid4())
    await db_session.commit()
    submit = AsyncMock()
    monkeypatch.setattr(orchestrator_client, "publish_project_cell", submit)
    response = await client.post(f'/api/projects/{value["project"].id}/deploy', json={})
    assert response.status_code == (404 if reason == "other_owner" else 409), response.text
    submit.assert_not_awaited()


@pytest.mark.parametrize("exact", [False, True])
async def test_readiness_requires_published_snapshot_not_just_later_timestamp(
    client, db_session, monkeypatch, exact,
):
    value, _ = await seed(db_session)
    deployment = {
        "phase": "done", "prod_url": "https://qa.example.test",
        "finished_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "snapshot_id": str(value["snapshot"].id if exact else uuid4()),
        "commit_sha": value["snapshot"].commit_sha,
    }
    monkeypatch.setattr(orchestrator_client, "get_deploy", AsyncMock(return_value=deployment))
    response = await client.get(f'/api/projects/{value["project"].id}/max/readiness')
    assert response.status_code == 200, response.text
    statuses = {item["id"]: item["done"] for item in response.json()["items"]}
    assert statuses["build"] is True
    assert statuses["publish"] is exact


@pytest.mark.parametrize("failure", [False, True])
async def test_disconnect_revokes_public_credentials_before_deleting_integration(
    client, db_session, monkeypatch, failure,
):
    value, integration = await seed(db_session)
    calls = []

    async def configure(project_id, payload):
        assert await db_session.get(MaxIntegration, integration.id) is not None
        calls.append(payload)
        if failure:
            raise orchestrator_client.OrchestratorUnavailable("test public control unavailable")
        return {"applied": True}

    monkeypatch.setattr(orchestrator_client, "configure_published_cell", configure)
    response = await client.delete(f'/api/projects/{value["project"].id}/integrations/max')
    assert response.status_code == (503 if failure else 204), response.text
    assert calls == [{"owner_id": str(value["project"].owner_id), "runtime_env": {}}]
    assert (await db_session.get(MaxIntegration, integration.id) is not None) is failure
