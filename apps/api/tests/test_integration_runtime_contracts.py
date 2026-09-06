from __future__ import annotations

from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from omnia_api.core.crypto import encrypt_strong
from omnia_api.models.app_integration import BusinessIntegration
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.routers import integration_runtime
from omnia_api.services import integration_providers
from tests.test_app_integrations_api import _max_init_data, _register_and_create


async def connect(client, db_session, monkeypatch, provider="yookassa"):
    project = await _register_and_create(client, monkeypatch)

    async def verified(*args, **kwargs):
        return "Synthetic test account"

    monkeypatch.setattr(integration_providers, "verify_provider", verified)
    values = (
        {"shop_id": "123", "secret_key": "test-only-secret"}
        if provider == "yookassa"
        else {"webhook_url": "https://test.bitrix24.ru/rest/1/test-only-secret/"}
    )
    response = await client.put(
        f"/api/projects/{project}/app-integrations/{provider}", json={"values": values}
    )
    assert response.status_code == 200
    owner = await db_session.scalar(select(BusinessIntegration.created_by_user_id))
    db_session.add(
        MaxIntegration(
            project_id=UUID(project),
            owner_id=owner,
            bot_token_enc=encrypt_strong("test-max-token"),
            webhook_secret_enc=encrypt_strong("test-webhook"),
        )
    )
    await db_session.commit()
    return project


def upstream(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        integration_runtime.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def headers(user=42):
    return {"X-MAX-Init-Data": _max_init_data("test-max-token", user)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"omnia_project_id": "other", "max_user_id": "42"},
        {"omnia_project_id": "same", "max_user_id": "99"},
    ],
)
async def test_payment_status_rejects_foreign_payment(client, db_session, monkeypatch, metadata):
    project = await connect(client, db_session, monkeypatch)
    metadata = {k: project if v == "same" else v for k, v in metadata.items()}
    upstream(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "id": "payment-12345",
                "status": "pending",
                "metadata": metadata,
                "confirmation": {"confirmation_url": "https://payment.example/private-link"},
            },
        ),
    )
    response = await client.post(
        f"/api/runtime/projects/{project}/payments/status",
        headers=headers(),
        json={"payment_id": "payment-12345"},
    )
    assert response.status_code == 404
    assert "private-link" not in response.text


@pytest.mark.asyncio
async def test_owned_payment_status_returns_provider_state(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    upstream(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "id": "payment-12345",
                "status": "succeeded",
                "metadata": {"omnia_project_id": project, "max_user_id": "42"},
            },
        ),
    )
    response = await client.post(
        f"/api/runtime/projects/{project}/payments/status",
        headers=headers(),
        json={"payment_id": "payment-12345"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"id": "payment-12345", "status": "invented"},
        {"id": "payment-12345", "status": "pending", "confirmation": []},
    ],
)
async def test_payment_create_rejects_malformed_success(client, db_session, monkeypatch, body):
    project = await connect(client, db_session, monkeypatch)
    upstream(monkeypatch, lambda request: httpx.Response(200, json=body))
    response = await client.post(
        f"/api/runtime/projects/{project}/payments",
        headers=headers(),
        json={
            "amount": "100.00",
            "description": "Test payment",
            "return_url": "https://example.com/",
            "idempotency_key": "test-request-key-123",
        },
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "integration_response_invalid"


@pytest.mark.asyncio
async def test_shop_keys_stable_per_user_distinct_across_users(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    keys = []

    def provider(request):
        keys.append(request.headers["Idempotence-Key"])
        return httpx.Response(200, json={"id": "payment-12345", "status": "pending"})

    upstream(monkeypatch, provider)
    for user in (42, 42, 43):
        response = await client.post(
            f"/api/runtime/projects/{project}/payments",
            headers=headers(user),
            json={
                "amount": "100.00",
                "description": "Test payment",
                "return_url": "https://example.com/",
                "idempotency_key": "same-client-key-123",
            },
        )
        assert response.status_code == 200
    assert keys[0] == keys[1]
    assert keys[0] != keys[2]
    assert all(len(key) <= 64 for key in keys)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"error": "ACCESS_DENIED", "error_description": "test-only-secret"},
        {},
        {"result": False},
        {"result": {}},
    ],
)
async def test_crm_never_reports_error_as_created(client, db_session, monkeypatch, body):
    project = await connect(client, db_session, monkeypatch, "bitrix24")
    upstream(monkeypatch, lambda request: httpx.Response(200, json=body))
    response = await client.post(
        f"/api/runtime/projects/{project}/leads", headers=headers(), json={"name": "Test"}
    )
    assert response.status_code in (422, 502)
    assert "test-only-secret" not in response.text


@pytest.mark.asyncio
async def test_crm_replay_returns_same_lead_and_rejects_changed_payload(
    client, db_session, monkeypatch
):
    project = await connect(client, db_session, monkeypatch, "bitrix24")
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"result": 123})

    upstream(monkeypatch, provider)
    data = {"name": "Test", "idempotency_key": "lead-request-key-123"}
    url = f"/api/runtime/projects/{project}/leads"
    first = await client.post(url, headers=headers(), json=data)
    second = await client.post(url, headers=headers(), json=data)
    changed = await client.post(url, headers=headers(), json={**data, "name": "Changed"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"provider": "bitrix24", "id": "123"}
    assert changed.status_code == 409
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_crm_lost_response_is_not_blindly_replayed(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch, "bitrix24")
    accepted = []

    def provider(request):
        accepted.append("lead-123")
        raise httpx.ReadTimeout("accepted but response lost", request=request)

    upstream(monkeypatch, provider)
    data = {"name": "Test", "idempotency_key": "lead-request-key-123"}
    url = f"/api/runtime/projects/{project}/leads"
    first = await client.post(url, headers=headers(), json=data)
    second = await client.post(url, headers=headers(), json=data)
    assert first.status_code == second.status_code == 409
    assert first.json()["error"]["code"] == "integration_operation_unknown"
    assert accepted == ["lead-123"]


@pytest.mark.asyncio
async def test_crm_receipt_survives_business_reconnection(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch, "bitrix24")
    sent = []

    def provider(request):
        sent.append(request)
        return httpx.Response(200, json={"result": 123})

    upstream(monkeypatch, provider)
    payload = {"name": "Test", "idempotency_key": "lead-request-key-123"}
    url = f"/api/runtime/projects/{project}/leads"
    assert (await client.post(url, headers=headers(), json=payload)).status_code == 200
    assert (
        await client.delete(f"/api/projects/{project}/app-integrations/bitrix24/business")
    ).status_code == 204
    assert (
        await client.put(
            f"/api/projects/{project}/app-integrations/bitrix24",
            json={"values": {"webhook_url": "https://test.bitrix24.ru/rest/1/new-test-secret/"}},
        )
    ).status_code == 200
    replay = await client.post(url, headers=headers(), json=payload)
    assert replay.status_code == 200
    assert replay.json()["id"] == "123"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_crm_concurrent_submission_and_cancelled_dispatch_never_replay(
    client,
    db_session,
    monkeypatch,
    test_engine,
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from omnia_api.core.errors import ApiError
    from omnia_api.services.integration_operations import execute_once

    project = await connect(client, db_session, monkeypatch, "bitrix24")
    connection_id = await db_session.scalar(select(BusinessIntegration.id))
    entered, release = asyncio.Event(), asyncio.Event()
    dispatched = []

    async def send():
        dispatched.append(1)
        entered.set()
        await release.wait()
        return {"provider": "bitrix24", "id": "123"}

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    kwargs = dict(
        project_id=UUID(project),
        integration_id=connection_id,
        max_user_id=42,
        provider="bitrix24",
        kind="lead",
        client_key="concurrent-request-key",
        payload={"name": "Test"},
        send=send,
    )
    async with factory() as first, factory() as second:
        running = asyncio.create_task(execute_once(first, **kwargs))
        await asyncio.wait_for(entered.wait(), 3)
        try:
            with pytest.raises(ApiError) as duplicate:
                await execute_once(second, **kwargs)
            assert duplicate.value.code == "integration_operation_unknown"
        finally:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
    async with factory() as after_restart:
        with pytest.raises(ApiError) as retry:
            await execute_once(after_restart, **kwargs)
        assert retry.value.code == "integration_operation_unknown"
    assert dispatched == [1]


@pytest.mark.asyncio
async def test_catalog_only_advertises_implemented_connected_operations(
    client, db_session, monkeypatch
):
    project = await connect(client, db_session, monkeypatch)
    catalog = await client.get(f"/api/projects/{project}/app-integrations")
    providers = {p["key"]: p for p in catalog.json()["providers"]}
    assert "Возвраты" not in providers["yookassa"]["capabilities"]
    assert "Заказы" not in providers["iiko"]["capabilities"]
    runtime = await client.get(f"/api/runtime/projects/{project}/integrations", headers=headers())
    assert runtime.status_code == 200
    assert runtime.json()["capabilities"] == ["Оплата", "Статусы"]


@pytest.mark.asyncio
async def test_generation_context_uses_only_ready_bound_methods_without_credentials(
    client, db_session, monkeypatch
):
    from omnia_api.services.integration_generation import generation_context

    project = await connect(client, db_session, monkeypatch)
    context = await generation_context(db_session, UUID(project))
    assert "createOmniaPayment" in context
    assert "getOmniaPayment" in context
    assert "test-only-secret" not in context
    assert "createOmniaLead" not in context
    assert (
        await client.delete(f"/api/projects/{project}/app-integrations/yookassa")
    ).status_code == 204
    assert "createOmniaPayment" not in await generation_context(db_session, UUID(project))


async def test_payment_retry_after_reconnection_does_not_duplicate_lost_response(
    client, db_session, monkeypatch
):
    project = await connect(client, db_session, monkeypatch)
    original_connection = await db_session.scalar(select(BusinessIntegration.id))
    payments = {}
    lose_first_response = True

    def provider(request):
        nonlocal lose_first_response
        key = request.headers["Idempotence-Key"]
        payment_id = payments.setdefault(key, f"payment-{len(payments) + 1:05d}")
        if lose_first_response:
            lose_first_response = False
            raise httpx.ReadTimeout("response lost after payment creation", request=request)
        return httpx.Response(200, json={"id": payment_id, "status": "pending"})

    upstream(monkeypatch, provider)
    payload = {
        "amount": "100.00",
        "description": "Test payment",
        "return_url": "https://example.com/",
        "idempotency_key": "reconnected-shop-payment-123",
    }
    url = f"/api/runtime/projects/{project}/payments"
    assert (await client.post(url, headers=headers(), json=payload)).status_code == 503
    assert len(payments) == 1
    assert (
        await client.delete(f"/api/projects/{project}/app-integrations/yookassa/business")
    ).status_code == 204
    assert (
        await client.put(
            f"/api/projects/{project}/app-integrations/yookassa",
            json={"values": {"shop_id": "123", "secret_key": "synthetic-new-secret"}},
        )
    ).status_code == 200
    assert await db_session.scalar(select(BusinessIntegration.id)) != original_connection
    replay = await client.post(url, headers=headers(), json=payload)
    assert replay.status_code == 200
    assert replay.json()["id"] == "payment-00001"
    assert len(payments) == 1
