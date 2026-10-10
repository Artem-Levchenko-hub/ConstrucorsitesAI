"""Exact authenticated legacy reconciliation on disposable PostgreSQL; no CRM calls."""

import hashlib
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from tests.test_amocrm_native_api import DATA, connected
from tests.test_integration_runtime_contracts import headers
from yleum_api.models.integration_operation import IntegrationOperation
from yleum_api.routers import amocrm as router
from yleum_api.schemas.integration_runtime import RuntimeLeadRequest

KEY = "c68421c9-934a-48f8-b293-147d5197a8b9"
DIGEST = hashlib.sha256(
    json.dumps(
        RuntimeLeadRequest.model_validate(DATA).model_dump(exclude={"idempotency_key"}),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


async def seed(client, db_session, monkeypatch, **changes):
    project, connection = await connected(client, db_session, monkeypatch)

    def forbidden_provider(*args, **kwargs):
        raise AssertionError("Receipt lookup must not open a CRM HTTP client")

    monkeypatch.setattr(router.amocrm, "client", forbidden_provider)
    values = dict(
        project_id=UUID(project),
        integration_id=connection.id,
        provider="amocrm",
        max_user_id="42",
        kind="lead",
        client_key=KEY,
        request_digest=DIGEST,
        status="succeeded",
        result={
            "provider": "amocrm",
            "id": "2964609",
            "_account_base_url": "https://example.amocrm.ru",
            "details_status": "recorded",
            "name": "Never return customer payload",
            "warning": "Never echo raw stored text",
        },
    )
    values.update(changes)
    row = IntegrationOperation(**values)
    db_session.add(row)
    await db_session.commit()
    return project, connection, row


async def lookup(client, project, **payload):
    return await client.post(
        f"/api/runtime/projects/{project}/leads/receipt",
        headers=headers(),
        json={"idempotency_key": KEY, **payload},
    )


@pytest.mark.asyncio
async def test_exact_success_is_read_only_and_contains_no_personal_payload(
    client,
    db_session,
    monkeypatch,
):
    project, _, row = await seed(client, db_session, monkeypatch)
    original = dict(row.result)
    for _ in range(2):
        response = await lookup(
            client, project, expected_request_digest=DIGEST, expected_lead_id="2964609"
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "provider": "amocrm",
            "id": "2964609",
            "status": "succeeded",
            "idempotency_key": KEY,
            "request_digest": DIGEST,
            "details_status": "recorded",
            "warning": None,
        }
        assert response.headers["cache-control"] == "private, no-store"
    await db_session.refresh(row)
    assert row.result == original and row.status == "succeeded"
    assert len((await db_session.scalars(select(IntegrationOperation))).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["dispatching", "unknown", "rejected"])
async def test_non_success_stays_quarantined(client, db_session, monkeypatch, status):
    project, _, _ = await seed(client, db_session, monkeypatch, status=status)
    response = await lookup(client, project)
    assert response.status_code == 409
    assert "2964609" not in response.text and DIGEST not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "bitrix24"},
        {"kind": "lead_note"},
        {"max_user_id": "99"},
        {"integration_id": None},
        {
            "result": {
                "provider": "amocrm",
                "id": "2964609",
                "_account_base_url": "https://different.amocrm.ru",
            }
        },
    ],
)
async def test_exact_scope_prevents_foreign_or_changed_connection_reads(
    client,
    db_session,
    monkeypatch,
    changes,
):
    project, _, _ = await seed(client, db_session, monkeypatch, **changes)
    response = await lookup(client, project)
    assert response.status_code == 404
    assert "2964609" not in response.text


@pytest.mark.asyncio
async def test_authentication_and_foreign_project_cannot_find_key(client, db_session, monkeypatch):
    project, _, _ = await seed(client, db_session, monkeypatch)
    url = f"/api/runtime/projects/{project}/leads/receipt"
    assert (await client.post(url, json={"idempotency_key": KEY})).status_code == 401
    assert (
        await client.post(url, headers=headers(99), json={"idempotency_key": KEY})
    ).status_code == 404
    foreign = await lookup(client, uuid4())
    assert foreign.status_code == 409  # existing runtime requires a configured MAX project
    assert "2964609" not in foreign.text and DIGEST not in foreign.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"expected_request_digest": "0" * 64},
        {"expected_lead_id": "2964610"},
        {"idempotency_key": "c68421c9-934a-48f8-b293-147d5197a8ba"},
    ],
)
async def test_key_id_or_digest_mismatch_never_adopts(client, db_session, monkeypatch, payload):
    project, _, _ = await seed(client, db_session, monkeypatch)
    response = await lookup(client, project, **payload)
    assert response.status_code == (404 if "idempotency_key" in payload else 409)
    assert "2964609" not in response.text and DIGEST not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"request_digest": "bad-digest"},
        {
            "result": {
                "provider": "amocrm",
                "id": "../other",
                "_account_base_url": "https://example.amocrm.ru",
            }
        },
        {
            "result": {
                "provider": "bitrix24",
                "id": "2964609",
                "_account_base_url": "https://example.amocrm.ru",
            }
        },
    ],
)
async def test_invalid_stored_receipt_is_not_public_success(
    client,
    db_session,
    monkeypatch,
    changes,
):
    project, _, _ = await seed(client, db_session, monkeypatch, **changes)
    response = await lookup(client, project)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_ambiguous_legacy_duplicates_are_not_selected(client, db_session, monkeypatch):
    project, _, row = await seed(client, db_session, monkeypatch)
    # Only this disposable test schema: reproduce an inconsistent legacy DB.
    await db_session.execute(
        text("ALTER TABLE integration_operations DROP CONSTRAINT uq_integration_operation_key")
    )
    db_session.add(
        IntegrationOperation(
            project_id=row.project_id,
            integration_id=row.integration_id,
            provider=row.provider,
            max_user_id=row.max_user_id,
            kind=row.kind,
            client_key=row.client_key,
            request_digest=row.request_digest,
            status=row.status,
            result=dict(row.result),
        )
    )
    await db_session.commit()
    response = await lookup(client, project)
    assert response.status_code == 409 and "2964609" not in response.text


@pytest.mark.asyncio
async def test_unknown_details_are_explicit_and_never_echo_stored_warning(
    client,
    db_session,
    monkeypatch,
):
    result = {
        "provider": "amocrm",
        "id": "2964609",
        "_account_base_url": "https://example.amocrm.ru",
        "details_status": "unknown",
        "warning": "Unsafe stored customer or credential text",
    }
    project, _, _ = await seed(client, db_session, monkeypatch, result=result)
    response = await lookup(client, project)
    assert response.status_code == 200
    assert response.json()["details_status"] == "unknown" and response.json()["warning"]
    assert "Unsafe" not in response.text
