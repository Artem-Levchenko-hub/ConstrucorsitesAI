"""Managed history is a durable actor/project receipt, never another provider write."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from tests.test_integration_runtime_contracts import connect, headers
from yleum_api.models.app_integration import AccountIntegration
from yleum_api.models.integration_operation import IntegrationOperation
from yleum_api.models.max_integration import MaxIntegration
from yleum_api.models.project import Project


async def seed(client, db_session, monkeypatch, *, state="succeeded", result=None):
    project = await connect(client, db_session, monkeypatch, "moysklad")
    connection = await db_session.scalar(select(AccountIntegration))
    row = IntegrationOperation(
        project_id=UUID(project),
        integration_id=connection.id,
        provider="moysklad",
        max_user_id="42",
        kind="customer_order",
        client_key="qa-stable-order-intent",
        request_digest="a" * 64,
        status=state,
        result=result if result is not None else {"provider": "moysklad", "id": str(UUID(int=9))},
        finished_at=datetime.now(UTC) if state == "succeeded" else None,
    )
    db_session.add(row)
    await db_session.commit()
    return project, row


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["succeeded", "dispatching", "unknown", "rejected"])
async def test_status_is_scoped_durable_state_without_provider(
    client, db_session, monkeypatch, state
):
    project, row = await seed(client, db_session, monkeypatch, state=state)
    response = await client.post(
        f"/api/runtime/projects/{project}/orders/status",
        headers=headers(),
        json={"idempotency_key": row.client_key},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == state
    assert body["idempotency_key"] == row.client_key
    assert body["order"] is not None if state == "succeeded" else body["order"] is None
    assert body["snapshot_status"] == "unavailable"
    assert all(word not in response.text for word in ("request_digest", "max_user_id", "token"))
    await db_session.refresh(row)
    assert row.status == state  # Read cannot reconcile or dispatch an unknown result.


@pytest.mark.asyncio
async def test_foreign_actor_and_project_cannot_read_known_id_or_intent(
    client, db_session, monkeypatch
):
    project, row = await seed(client, db_session, monkeypatch)
    original = await db_session.get(Project, UUID(project))
    other = Project(
        owner_id=original.owner_id, name="Other QA", slug=str(uuid4()), template=original.template
    )
    db_session.add(other)
    await db_session.flush()
    binding = await db_session.scalar(select(MaxIntegration))
    db_session.add(
        MaxIntegration(
            project_id=other.id,
            owner_id=original.owner_id,
            bot_token_enc=binding.bot_token_enc,
            webhook_secret_enc=binding.webhook_secret_enc,
        )
    )
    await db_session.commit()
    for target, actor in [(project, 43), (str(other.id), 42)]:
        for operation, payload in [
            ("status", {"idempotency_key": row.client_key}),
            ("details", {"order_id": row.result["id"]}),
        ]:
            response = await client.post(
                f"/api/runtime/projects/{target}/orders/{operation}",
                headers=headers(actor),
                json=payload,
            )
            assert response.status_code == 404
            assert row.result["id"] not in response.text
        response = await client.get(
            f"/api/runtime/projects/{target}/orders", headers=headers(actor)
        )
        assert response.status_code == 200 and response.json()["items"] == []
    assert (await client.get(f"/api/runtime/projects/{project}/orders")).status_code == 401


@pytest.mark.asyncio
async def test_legacy_receipt_survives_disconnect_without_fabricated_snapshot(
    client, db_session, monkeypatch
):
    project, row = await seed(client, db_session, monkeypatch)
    row.integration_id = None
    row.result = {**row.result, "_private": "not-public"}
    await db_session.commit()
    response = await client.post(
        f"/api/runtime/projects/{project}/orders/details",
        headers=headers(),
        json={"order_id": row.result["id"]},
    )
    assert response.status_code == 200
    assert response.json()["order"]["id"] == row.result["id"]
    assert response.json()["order"]["snapshot"] is None
    assert response.json()["snapshot_status"] == "unavailable"
    assert "not-public" not in response.text


@pytest.mark.asyncio
async def test_snapshot_list_does_not_expose_other_kinds_and_reports_more(
    client, db_session, monkeypatch
):
    project, _row = await seed(client, db_session, monkeypatch)
    for i in range(21):
        db_session.add(
            IntegrationOperation(
                project_id=UUID(project),
                provider="moysklad",
                max_user_id="42",
                kind="customer_order",
                client_key=f"qa-more-{i:020d}",
                request_digest="a" * 64,
                status="unknown",
                result={},
            )
        )
    db_session.add(
        IntegrationOperation(
            project_id=UUID(project),
            provider="moysklad",
            max_user_id="42",
            kind="lead",
            client_key="not-an-order",
            request_digest="a" * 64,
            status="unknown",
            result={},
        )
    )
    await db_session.commit()
    response = await client.get(f"/api/runtime/projects/{project}/orders", headers=headers())
    assert response.status_code == 200
    assert len(response.json()["items"]) == 20 and response.json()["has_more"] is True
    assert "not-an-order" not in response.text


@pytest.mark.asyncio
async def test_unknown_dispatch_preserves_snapshot_and_never_resends(
    client, db_session, monkeypatch
):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.integration_operations import execute_once

    project = await connect(client, db_session, monkeypatch, "moysklad")
    connection = await db_session.scalar(select(AccountIntegration))
    snapshot = {
        "amount_source": "server_submitted",
        "lines": [],
        "total_minor": "29900",
        "currency": "RUB",
    }
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        raise ApiError("integration_provider_unavailable", "ambiguous response", 503)

    args = dict(
        project_id=UUID(project),
        integration_id=connection.id,
        provider="moysklad",
        max_user_id=42,
        kind="customer_order",
        client_key="unknown-qa-intent-key",
        payload={"synthetic": True},
        send=send,
        dispatch_result=lambda: {"snapshot": snapshot},
    )
    for _ in range(2):
        with pytest.raises(ApiError) as error:
            await execute_once(db_session, **args)
        assert error.value.code == "integration_operation_unknown"
    response = await client.post(
        f"/api/runtime/projects/{project}/orders/status",
        headers=headers(),
        json={"idempotency_key": args["client_key"]},
    )
    assert response.status_code == 200 and response.json()["status"] == "unknown"
    assert response.json()["submitted_snapshot"] == snapshot
    assert response.json()["order"] is None and calls == 1
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_concurrent_same_intent_one_effect_and_replay_same_receipt(
    client, db_session, test_engine, monkeypatch
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.core.errors import ApiError
    from yleum_api.services.integration_operations import execute_once

    project = await connect(client, db_session, monkeypatch, "moysklad")
    connection = await db_session.scalar(select(AccountIntegration))
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0
    result = {
        "provider": "moysklad",
        "id": str(UUID(int=999)),
        "snapshot": {
            "amount_source": "server_submitted",
            "lines": [],
            "total_minor": "29900",
            "currency": "RUB",
        },
    }

    async def send():
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return result

    args = dict(
        project_id=UUID(project),
        integration_id=connection.id,
        provider="moysklad",
        max_user_id=42,
        kind="customer_order",
        client_key="parallel-qa-intent-key",
        payload={"line": 1},
        send=send,
        dispatch_result=lambda: {"snapshot": result["snapshot"]},
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as first, factory() as second:
        task = asyncio.create_task(execute_once(first, **args))
        await asyncio.wait_for(entered.wait(), 5)
        try:
            with pytest.raises(ApiError) as error:
                await execute_once(second, **args)
            assert error.value.code == "integration_operation_unknown"
        finally:
            finish.set()
        assert await asyncio.wait_for(task, 5) == result
    async with factory() as replay:
        assert await execute_once(replay, **args) == result
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("product_name", ["Synthetic product", None])
async def test_http_order_ignores_browser_price_replay_and_history_are_frozen(
    client, db_session, monkeypatch, product_name
):
    import json

    import httpx

    from tests.test_integration_runtime_contracts import upstream

    project = await connect(client, db_session, monkeypatch, "moysklad")
    calls = []

    def provider(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/entity/organization") or path.endswith("/entity/store"):
            return httpx.Response(200, json={"rows": [{"id": str(UUID(int=2))}]})
        if path.endswith(f"/entity/product/{UUID(int=1)}"):
            return httpx.Response(
                200,
                json={
                    "id": str(UUID(int=1)),
                    "name": product_name,
                    "salePrices": [{"value": 29900}],
                },
            )
        if path.endswith("/report/stock/all/current"):
            return httpx.Response(200, json=[{"assortmentId": str(UUID(int=1)), "freeStock": 10}])
        if path.endswith("/entity/counterparty"):
            return httpx.Response(200, json={"rows": [{"id": str(UUID(int=3))}]})
        if path.endswith("/entity/customerorder"):
            assert json.loads(request.content)["positions"][0]["price"] == 29900
            return httpx.Response(200, json={"id": str(UUID(int=4))})
        raise AssertionError("Unexpected provider call")

    upstream(monkeypatch, provider)
    body = {
        "idempotency_key": "qa-server-price-order-key",
        "buyer_name": "QA private buyer",
        "lines": [{"product_id": str(UUID(int=1)), "quantity": 1, "price": 0}],
        "total": 0,
    }
    first = await client.post(
        f"/api/runtime/projects/{project}/orders", headers=headers(), json=body
    )
    assert first.status_code == 200
    assert first.json()["snapshot"]["total_minor"] == "29900.00"
    assert first.json()["snapshot"]["currency"] is None
    assert first.json()["snapshot"]["lines"][0]["name"] == product_name
    count = len(calls)
    replay = await client.post(
        f"/api/runtime/projects/{project}/orders", headers=headers(), json=body
    )
    history = await client.get(f"/api/runtime/projects/{project}/orders", headers=headers())
    assert replay.json() == first.json() and len(calls) == count
    assert history.json()["items"][0]["submitted_snapshot"]["total_minor"] == "29900.00"
    receipt = await db_session.scalar(select(IntegrationOperation))
    assert "QA private buyer" not in json.dumps(receipt.result)
    assert history.json()["items"][0]["submitted_snapshot"]["currency"] is None
