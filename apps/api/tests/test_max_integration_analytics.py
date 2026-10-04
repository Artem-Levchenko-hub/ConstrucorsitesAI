"""Confirmed integration receipts count actions; provider writes stay authoritative."""

from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text

from tests.test_amocrm_native_api import DATA, connected
from tests.test_integration_runtime_contracts import connect, headers, upstream
from yleum_api.models.integration_operation import IntegrationOperation
from yleum_api.models.max_analytics import MaxAnalyticsEvent
from yleum_api.services.max_analytics import aggregate_events


async def test_signed_confirmed_lead_with_unknown_note_counts_once(client, db_session, monkeypatch):
    project, _connection = await connected(client, db_session, monkeypatch)
    writes = []

    def provider(request):
        writes.append(request.url.path)
        if request.url.path.endswith("/complex"):
            return httpx.Response(200, json=[{"id": 123}])
        raise httpx.ReadTimeout("QA_NOTE_RESPONSE_LOST", request=request)

    upstream(monkeypatch, provider)
    url = f"/api/runtime/projects/{project}/leads"
    rejected = await client.post(url, headers=headers(), json={**DATA, "name": ""})
    assert rejected.status_code == 422 and not writes
    assert not list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    first = await client.post(url, headers=headers(), json=DATA)
    replay = await client.post(url, headers=headers(), json=DATA)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["details_status"] == "unknown"
    assert first.json()["warning"]
    assert len(writes) == 2
    receipts = list((await db_session.scalars(select(IntegrationOperation))).all())
    lead = next(r for r in receipts if r.kind == "lead")
    assert lead.status == "succeeded"
    assert next(r for r in receipts if r.kind == "lead_note").status == "unknown"
    events = list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    assert len(events) == 1
    assert events[0].event_id == lead.id and events[0].kind == "action"
    result = await aggregate_events(db_session, lead.project_id, 7)
    assert result["actions"] == 1 and result["users"] == 1


async def test_collector_setup_failure_does_not_escape_confirmed_result(db_session, monkeypatch):
    from yleum_api.services import max_integration_analytics

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("QA_ANALYTICS_FACTORY_FAILURE")

    monkeypatch.setattr(max_integration_analytics, "async_sessionmaker", unavailable)
    await max_integration_analytics.record_confirmed_action(
        db_session,
        project_id=uuid4(),
        user_id=42,
        provider="amocrm",
        kind="lead",
        client_key="QA_FACTORY_FAILED_RECEIPT",
    )


async def test_only_committed_creation_receipts_in_exact_actor_project_scope_count(
    client, db_session, monkeypatch
):
    from yleum_api.services.max_integration_analytics import record_confirmed_action

    project, _ = await connected(client, db_session, monkeypatch)
    project_id = UUID(project)
    rows = []
    for kind, state, actor in [
        ("lead", "succeeded", "42"),
        ("lead_note", "succeeded", "42"),
        ("lead", "unknown", "42"),
        ("lead", "rejected", "42"),
        ("lead", "dispatching", "42"),
        ("lead", "succeeded", "99"),
    ]:
        row = IntegrationOperation(
            project_id=project_id,
            provider="amocrm",
            max_user_id=actor,
            kind=kind,
            status=state,
            client_key=str(uuid4()),
            request_digest="qa-only",
            result={},
        )
        db_session.add(row)
        rows.append(row)
    await db_session.commit()
    for row in rows:
        await record_confirmed_action(
            db_session,
            project_id=project_id,
            user_id=42,
            provider="amocrm",
            kind=row.kind,
            client_key=row.client_key,
        )
    valid = rows[0]
    valid_id = valid.id
    for changed in [
        {"project_id": uuid4()},
        {"provider": "bitrix24"},
        {"client_key": "missing"},
        {"user_id": 43},
        {"user_id": 0},
    ]:
        args = dict(
            project_id=project_id,
            user_id=42,
            provider="amocrm",
            kind="lead",
            client_key=valid.client_key,
        )
        args.update(changed)
        await record_confirmed_action(db_session, **args)
    await record_confirmed_action(
        db_session,
        project_id=project_id,
        user_id=42,
        provider="amocrm",
        kind="lead",
        client_key=valid.client_key,
    )
    pending = IntegrationOperation(
        project_id=project_id,
        provider="amocrm",
        max_user_id="42",
        kind="lead",
        status="succeeded",
        client_key="QA_UNCOMMITTED",
        request_digest="qa-only",
        result={},
    )
    db_session.add(pending)
    await db_session.flush()
    await record_confirmed_action(
        db_session,
        project_id=project_id,
        user_id=42,
        provider="amocrm",
        kind="lead",
        client_key=pending.client_key,
    )
    assert pending in db_session and db_session.in_transaction()
    await db_session.rollback()
    events = list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    assert len(events) == 1 and events[0].event_id == valid_id
    assert (await aggregate_events(db_session, project_id, 7))["actions"] == 1
    assert (await aggregate_events(db_session, uuid4(), 7))["actions"] == 0
    foreign_actor = (
        await db_session.scalars(
            select(IntegrationOperation).where(IntegrationOperation.max_user_id == "99")
        )
    ).one()
    await record_confirmed_action(
        db_session,
        project_id=project_id,
        user_id=99,
        provider="amocrm",
        kind="lead",
        client_key=foreign_actor.client_key,
    )
    events = list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    assert len(events) == 2 and len({event.actor_key for event in events}) == 2
    totals = await aggregate_events(db_session, project_id, 7)
    assert totals["actions"] == 2 and totals["users"] == 2


async def test_analytics_database_failure_keeps_success_and_retry_never_resends_provider(
    client, db_session, monkeypatch
):
    from yleum_api.services import max_analytics

    project, _ = await connected(client, db_session, monkeypatch)
    writes = []

    def provider(request):
        writes.append(request.url.path)
        if request.url.path.endswith("/notes"):
            return httpx.Response(200, json={"_embedded": {"notes": [{"id": 567}]}})
        return httpx.Response(200, json=[{"id": 123}])

    upstream(monkeypatch, provider)
    original = max_analytics.record_event

    async def broken(session, *_args):
        await session.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(max_analytics, "record_event", broken)
    data = {**DATA, "comment": None}
    url = f"/api/runtime/projects/{project}/leads"
    first = await client.post(url, headers=headers(), json=data)
    assert first.status_code == 200 and len(writes) == 2
    assert not list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    lead = (
        await db_session.scalars(
            select(IntegrationOperation).where(IntegrationOperation.kind == "lead")
        )
    ).one()
    assert lead.status == "succeeded"
    monkeypatch.setattr(max_analytics, "record_event", original)
    replay = await client.post(url, headers=headers(), json=data)
    assert replay.status_code == 200 and replay.json() == first.json() and len(writes) == 2
    events = list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    assert len(events) == 1 and events[0].event_id == lead.id


async def test_signed_order_counts_actual_receipt_once_and_no_receipt_never_counts(
    client, db_session, monkeypatch
):
    from yleum_api.services import integration_moysklad_orders, integration_operations

    project = await connect(client, db_session, monkeypatch, "moysklad")
    order_id, buyer_id, product_id = (str(uuid4()) for _ in range(3))
    writes = []

    async def prepare(*_args, **_kwargs):
        return integration_moysklad_orders.PreparedOrder({}, "QA_BUYER", buyer_id, "QA", None)

    def provider(request):
        if request.method == "GET" and request.url.path.endswith("/entity/product"):
            return httpx.Response(200, json={"rows": []})
        assert request.method == "POST" and request.url.path.endswith("/entity/customerorder")
        writes.append(request.url.path)
        return httpx.Response(200, json={"id": order_id})

    monkeypatch.setattr(integration_moysklad_orders, "prepare_customer_order", prepare)
    upstream(monkeypatch, provider)
    data = {
        "buyer_name": "QA buyer",
        "lines": [{"product_id": product_id, "quantity": 1}],
        "idempotency_key": "QA_ORDER_DURABLE_12345",
    }
    url = f"/api/runtime/projects/{project}/orders"
    catalog = await client.get(f"/api/runtime/projects/{project}/catalog", headers=headers())
    assert catalog.status_code == 200 and not writes
    assert not list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
    first = await client.post(url, headers=headers(), json=data)
    replay = await client.post(url, headers=headers(), json=data)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == {"provider": "moysklad", "id": order_id}
    assert len(writes) == 1
    row = (await db_session.scalars(select(IntegrationOperation))).one()
    event = (await db_session.scalars(select(MaxAnalyticsEvent))).one()
    assert row.kind == "customer_order" and row.status == "succeeded" and event.event_id == row.id

    async def no_receipt(*_args, **_kwargs):
        return {"provider": "moysklad", "id": str(uuid4())}

    monkeypatch.setattr(integration_operations, "execute_once", no_receipt)
    response = await client.post(
        url, headers=headers(), json={**data, "idempotency_key": "QA_NO_RECEIPT_1234567"}
    )
    assert response.status_code == 200 and len(writes) == 1
    assert len(list((await db_session.scalars(select(MaxAnalyticsEvent))).all())) == 1


@pytest.mark.parametrize("failure", ["rejected", "unknown", "cancelled"])
async def test_real_failed_or_cancelled_dispatch_never_counts(
    client, db_session, monkeypatch, failure
):
    import asyncio

    from yleum_api.core.errors import ApiError
    from yleum_api.services.integration_operations import execute_once
    from yleum_api.services.max_integration_analytics import record_confirmed_action

    project, connection = await connected(client, db_session, monkeypatch)
    key = "QA_FAILED_OPERATION_" + failure
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        if failure == "cancelled":
            raise asyncio.CancelledError()
        raise ApiError(
            "integration_request_rejected"
            if failure == "rejected"
            else "integration_provider_unavailable",
            "QA failed",
            422,
        )

    with pytest.raises(asyncio.CancelledError if failure == "cancelled" else ApiError):
        await execute_once(
            db_session,
            project_id=UUID(project),
            integration_id=connection.id,
            provider="amocrm",
            max_user_id=42,
            kind="lead",
            client_key=key,
            payload={"qa": failure},
            send=send,
        )
    await record_confirmed_action(
        db_session,
        project_id=UUID(project),
        user_id=42,
        provider="amocrm",
        kind="lead",
        client_key=key,
    )
    row = (await db_session.scalars(select(IntegrationOperation))).one()
    assert row.status == ("dispatching" if failure == "cancelled" else failure)
    assert calls == 1 and not list((await db_session.scalars(select(MaxAnalyticsEvent))).all())
