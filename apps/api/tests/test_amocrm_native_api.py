"""Disposable PostgreSQL acceptance: no real provider writes or credentials."""

import json

import httpx
import pytest
from sqlalchemy import select

from tests.test_integration_runtime_contracts import connect, headers, upstream
from yleum_api.core.crypto import encrypt_strong
from yleum_api.models.app_integration import AccountIntegration, ProjectIntegrationBinding
from yleum_api.models.integration_operation import IntegrationOperation

PIPELINES = {
    "_embedded": {
        "pipelines": [
            {
                "id": 12,
                "name": "Coffee",
                "_embedded": {
                    "statuses": [
                        {"id": 34, "name": "New", "type": 0},
                        {"id": 35, "name": "Confirmed", "type": 0},
                        {"id": 142, "name": "Won", "type": 0},
                    ]
                },
            },
        ]
    }
}
DATA = {
    "name": "Alice",
    "phone": "+79000000000",
    "email": "alice@example.com",
    "comment": "Call tomorrow",
    "source": "Coffee catering",
    "idempotency_key": "native-amocrm-key-123",
}


async def connected(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch, "bitrix24")
    connection = await db_session.scalar(select(AccountIntegration))
    connection.provider = "amocrm"
    connection.public_config = {"base_url": "https://example.amocrm.ru"}
    connection.credentials_enc = encrypt_strong(json.dumps({"access_token": "synthetic-only"}))
    binding = await db_session.scalar(select(ProjectIntegrationBinding))
    binding.provider = "amocrm"
    binding.config = {"pipeline_id": 12, "status_id": 34}
    await db_session.commit()
    return project, connection


@pytest.mark.asyncio
@pytest.mark.parametrize("note_outcome", ["success", "rejected", "lost"])
async def test_lead_and_note_are_durable_separate_writes(
    client,
    db_session,
    monkeypatch,
    note_outcome,
):
    project, _ = await connected(client, db_session, monkeypatch)
    writes = []

    def provider(request):
        writes.append(request)
        if request.url.path.endswith("/complex"):
            body = json.loads(request.content)[0]
            assert body["pipeline_id"] == 12 and body["status_id"] == 34
            assert body["_embedded"]["contacts"][0]["name"] == "Alice"
            return httpx.Response(200, json=[{"id": 123}])
        assert request.url.path == "/api/v4/leads/123/notes"
        note = json.loads(request.content)[0]["params"]["text"]
        assert DATA["comment"] in note and DATA["source"] in note
        if note_outcome == "lost":
            raise httpx.ReadTimeout("accepted but response lost", request=request)
        if note_outcome == "rejected":
            return httpx.Response(400, json={"title": "Bad Request"})
        return httpx.Response(200, json={"_embedded": {"notes": [{"id": 567}]}})

    upstream(monkeypatch, provider)
    url = f"/api/runtime/projects/{project}/leads"
    first = await client.post(url, headers=headers(), json=DATA)
    replay = await client.post(url, headers=headers(), json=DATA)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["id"] == "123"
    assert first.json()["details_status"] == (
        "recorded" if note_outcome == "success" else "unknown"
    )
    assert bool(first.json()["warning"]) == (note_outcome != "success")
    assert len(writes) == 2  # Replay never recreates a lead OR an ambiguous note.
    receipts = (await db_session.scalars(select(IntegrationOperation))).all()
    assert {r.kind for r in receipts} == {"lead", "lead_note"}
    assert next(r for r in receipts if r.kind == "lead").status == "succeeded"
    assert DATA["comment"] not in json.dumps([r.result for r in receipts])


@pytest.mark.asyncio
async def test_status_roundtrip_checks_user_project_and_account_before_provider(
    client,
    db_session,
    monkeypatch,
):
    project, connection = await connected(client, db_session, monkeypatch)
    calls = []
    stage = 34

    def provider(request):
        calls.append(request)
        if request.url.path.endswith("/complex"):
            return httpx.Response(200, json=[{"id": 123}])
        if request.url.path.endswith("/notes"):
            return httpx.Response(200, json={"_embedded": {"notes": [{"id": 567}]}})
        if request.url.path.endswith("/pipelines"):
            return httpx.Response(200, json=PIPELINES)
        return httpx.Response(
            200,
            json={
                "id": 123,
                "name": "Alice",
                "pipeline_id": 12,
                "status_id": stage,
                "updated_at": 1780000000,
            },
        )

    upstream(monkeypatch, provider)
    url = f"/api/runtime/projects/{project}/leads"
    assert (await client.post(url, headers=headers(), json=DATA)).status_code == 200
    first = await client.get(url, headers=headers())
    assert first.status_code == 200
    assert first.json()["items"][0]["status_name"] == "New"
    assert first.json()["items"][0]["checked_at"]
    stage = 35
    second = await client.post(url + "/status", headers=headers(), json={"lead_id": "123"})
    assert second.status_code == 200 and second.json()["status_name"] == "Confirmed"
    before = len(calls)
    foreign = await client.post(url + "/status", headers=headers(99), json={"lead_id": "123"})
    assert foreign.status_code == 404
    assert (await client.get(url, headers=headers(99))).json()["items"] == []
    assert len(calls) == before
    foreign_project = await client.post(
        "/api/projects",
        json={
            "name": "Another project",
            "template": "max_miniapp",
        },
    )
    assert foreign_project.status_code == 201
    denied = await client.post(
        f"/api/runtime/projects/{foreign_project.json()['id']}/leads/status",
        headers=headers(),
        json={"lead_id": "123"},
    )
    assert denied.status_code == 409  # No bot/CRM binding in another project.
    assert len(calls) == before
    connection.public_config = {"base_url": "https://different.amocrm.ru"}
    await db_session.commit()
    denied_account = await client.post(url + "/status", headers=headers(), json={"lead_id": "123"})
    assert denied_account.status_code == 404 and len(calls) == before
    replay = await client.post(url, headers=headers(), json=DATA)
    assert replay.status_code == 409 and len(calls) == before


@pytest.mark.asyncio
async def test_project_settings_validate_against_live_metadata(client, db_session, monkeypatch):
    project, _ = await connected(client, db_session, monkeypatch)
    upstream(monkeypatch, lambda request: httpx.Response(200, json=PIPELINES))
    url = f"/api/projects/{project}/app-integrations/amocrm"
    options = await client.get(url + "/options")
    assert options.status_code == 200
    assert [s["id"] for s in options.json()["pipelines"][0]["statuses"]] == [34, 35]
    for settings in ({"pipeline_id": 99, "status_id": 34}, {"pipeline_id": 12, "status_id": 142}):
        assert (await client.put(url + "/settings", json=settings)).status_code == 422
    assert (
        await client.put(url + "/settings", json={"pipeline_id": 12, "status_id": 35})
    ).status_code == 200
    catalog = await client.get(f"/api/projects/{project}/app-integrations")
    assert catalog.json()["connections"][0]["binding_config"]["status_id"] == 35


@pytest.mark.asyncio
async def test_invalid_fields_fail_before_any_provider_dispatch(client, db_session, monkeypatch):
    project, _ = await connected(client, db_session, monkeypatch)
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(500)

    upstream(monkeypatch, provider)
    for invalid in ({"name": "   "}, {"email": "not-an-email"}, {"phone": "abc"}):
        response = await client.post(
            f"/api/runtime/projects/{project}/leads", headers=headers(), json={**DATA, **invalid}
        )
        assert response.status_code == 422
    assert calls == []
    legacy = await client.post(
        f"/api/runtime/projects/{project}/leads",
        headers=headers(),
        json={k: v for k, v in DATA.items() if k != "idempotency_key"},
    )
    assert legacy.status_code == 422 and calls == []
    assert (await db_session.scalars(select(IntegrationOperation))).all() == []
