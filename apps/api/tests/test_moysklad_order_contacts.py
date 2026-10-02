"""Submitted contacts belong to each order, not a shared mutable actor profile."""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from yleum_api.core.errors import ApiError
from yleum_api.schemas.integration_runtime import RuntimeLeadRequest, RuntimeOrderRequest
from yleum_api.services.integration_moysklad_orders import PreparedOrder, submit_customer_order

# Canonical digest produced by the unmodified a5 RuntimeOrderRequest.
LEGACY_REQUEST_DIGEST = "a77372efa88d869943d3e840e6e4e52b6a51a1b25a96a7c699a021ed5fe462c9"

LEGACY_EMPTY_PHONE_DIGEST = "39c4fd49f656bfbab67d00ddf02e60e919467e2e700ac42d2a83d3c6639c5ec4"


def order(**changes: object) -> RuntimeOrderRequest:
    return RuntimeOrderRequest.model_validate(
        {
            "idempotency_key": "one-contact-order-key",
            "buyer_name": "QA buyer",
            "phone": None,
            "lines": [{"product_id": str(UUID(int=1)), "quantity": "1"}],
            **changes,
        }
    )


@pytest.mark.parametrize("phone", ["123", "letters", "+123456", "+1234567890123456", "1234567x"])
def test_order_rejects_phone_using_existing_lead_policy(phone: str) -> None:
    with pytest.raises(ValidationError):
        RuntimeLeadRequest(name="QA", phone=phone)
    with pytest.raises(ValidationError) as error:
        order(phone=phone)
    assert error.value.errors()[0]["loc"] == ("phone",)
    assert error.value.errors()[0]["type"] == "value_error"


@pytest.mark.parametrize("phone", [None, "", "   ", " +7 (900) 000-00-00 ", "1234567"])
def test_order_validates_contact_like_lead_without_changing_receipt_payload(
    phone: str | None,
) -> None:
    parsed = order(buyer_name="  QA buyer  ", phone=phone)
    lead = RuntimeLeadRequest(name="  QA buyer  ", phone=phone)
    assert parsed.buyer_name == "  QA buyer  "
    assert parsed.phone == phone
    assert parsed.buyer_name.strip() == lead.name
    assert (parsed.phone.strip() or None if parsed.phone is not None else None) == lead.phone


@pytest.mark.parametrize(
    "phone,digest",
    [(" +7 (900) 000-00-00 ", LEGACY_REQUEST_DIGEST), ("", LEGACY_EMPTY_PHONE_DIGEST)],
)
def test_order_retains_legacy_raw_contact_in_operation_digest(phone, digest) -> None:
    raw = {
        **order().model_dump(mode="json"),
        "buyer_name": "  Legacy QA buyer  ",
        "phone": phone,
    }
    parsed = RuntimeOrderRequest.model_validate(raw).model_dump(mode="json")
    assert parsed == raw
    encoded = json.dumps(parsed, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    assert hashlib.sha256(encoded.encode()).hexdigest() == digest


@pytest.mark.parametrize("name", ["", "   ", "QA" * 101])
def test_order_rejects_empty_or_oversize_normalized_buyer(name: str) -> None:
    with pytest.raises(ValidationError):
        order(buyer_name=name)


@pytest.mark.asyncio
@pytest.mark.parametrize("buyer_state", ["existing", "new", "appeared-after-preflight"])
async def test_order_snapshots_new_contact_without_mutating_actor_or_history(
    buyer_state: str,
) -> None:
    buyer = UUID(int=5)
    historical_order = {"description": "Earlier QA contact", "agent_name": "Earlier QA buyer"}
    historical_buyer = {"id": str(buyer), "name": "Earlier QA buyer", "phone": "+79000000001"}
    history = deepcopy((historical_order, historical_buyer))
    posted_orders: list[dict] = []
    posted_buyers: list[dict] = []
    requests: list[tuple[str, str]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/entity/counterparty"):
            return httpx.Response(
                200, json={"rows": [] if buyer_state == "new" else [historical_buyer]}
            )
        if request.method == "POST" and request.url.path.endswith("/entity/counterparty"):
            posted_buyers.append(json.loads(request.content))
            return httpx.Response(200, json={"id": str(buyer)})
        if request.method == "POST" and request.url.path.endswith("/entity/customerorder"):
            posted_orders.append(json.loads(request.content))
            return httpx.Response(200, json={"id": str(UUID(int=9 + len(posted_orders)))})
        raise AssertionError("Provider profile/history must never be updated")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        for index, (name, phone) in enumerate(
            [
                ("  New QA buyer  ", " +7 (900) 000-00-00 "),
                ("  Another QA recipient  ", None),
            ]
        ):
            parsed = order(buyer_name=name, phone=phone)
            prepared = PreparedOrder(
                payload={"externalCode": f"own-order-{index}", "positions": [{"quantity": 1}]},
                customer_code="stable-project-actor",
                counterparty_id=str(buyer) if buyer_state == "existing" or index else None,
                buyer_name=parsed.buyer_name,
                phone=parsed.phone,
            )
            await submit_customer_order(client, prepared)
            expected = (
                "Заказ из мини-приложения MAX через Yleum\nПокупатель: " + parsed.buyer_name.strip()
            )
            if parsed.phone:
                expected += "\nТелефон: " + parsed.phone.strip()
            assert posted_orders[-1]["description"] == expected
            assert posted_orders[-1]["agent"]["meta"]["href"].endswith(str(buyer))
            assert posted_orders[-1]["positions"] == [{"quantity": 1}]
            assert "stable-project-actor" not in expected
            assert f"own-order-{index}" not in expected
            if index == 0:
                first_snapshot = deepcopy(posted_orders[0])
            else:
                assert posted_orders[0] == first_snapshot
    assert len(posted_orders) == 2
    assert (historical_order, historical_buyer) == history
    assert not any(method in {"PUT", "PATCH", "DELETE"} for method, _ in requests)
    if buyer_state == "new":
        assert posted_buyers == [
            {
                "name": "New QA buyer",
                "externalCode": "stable-project-actor",
                "phone": "+7 (900) 000-00-00",
            }
        ]
    else:
        assert posted_buyers == []


@pytest.mark.asyncio
async def test_same_order_key_changed_contact_remains_conflict_before_provider() -> None:
    from tests.test_integration_operations_preflight import _Session
    from yleum_api.services.integration_operations import execute_once

    payload = order().model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    session = _Session(
        SimpleNamespace(request_digest=digest, status="succeeded", result={"id": "earlier-order"})
    )

    async def no_provider() -> None:
        raise AssertionError("Replay/conflict must not call provider")

    args = dict(
        project_id=uuid4(),
        integration_id=uuid4(),
        provider="moysklad",
        max_user_id=42,
        kind="customer_order",
        client_key=payload["idempotency_key"],
        prepare=no_provider,
        send=no_provider,
    )
    result = await execute_once(session, payload=payload, **args)  # type: ignore[arg-type]
    assert result == {"id": "earlier-order"}
    with pytest.raises(ApiError) as error:
        await execute_once(session, payload={**payload, "buyer_name": "Changed QA buyer"}, **args)  # type: ignore[arg-type]
    assert error.value.code == "integration_operation_conflict"
    assert error.value.status_code == 409
    assert session.insert_attempted is False


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [{"phone": "123"}, {"buyer_name": "   "}])
async def test_http_invalid_contact_never_reaches_actor_or_provider(monkeypatch, invalid) -> None:
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    from yleum_api.core.db import get_session
    from yleum_api.core.errors import validation_error_handler
    from yleum_api.routers import integration_runtime

    app = FastAPI()
    app.include_router(integration_runtime.router)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    async def unused_session():
        yield object()

    async def no_actor(*args, **kwargs):
        raise AssertionError("Invalid contacts must be rejected before actor/provider work")

    app.dependency_overrides[get_session] = unused_session
    monkeypatch.setattr(integration_runtime, "_runtime_context", no_actor)
    body = {**order().model_dump(mode="json"), **invalid}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        result = await client.post(f"/api/runtime/projects/{uuid4()}/orders", json=body)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "validation_failed"
    assert result.json()["error"]["details"]["errors"][0]["loc"] == ["body", next(iter(invalid))]


@pytest.mark.asyncio
async def test_http_orders_keep_actor_binding_and_independent_contact_snapshots(
    client,
    db_session,
    monkeypatch,
) -> None:
    from tests.test_integration_runtime_contracts import connect, headers, upstream

    project = await connect(client, db_session, monkeypatch, "moysklad")
    buyer_ids = {
        "yleum-max-" + hashlib.sha256(f"{project}:{actor}".encode()).hexdigest()[:32]: str(
            UUID(int=actor)
        )
        for actor in (42, 43)
    }
    posted = []

    def provider(request):
        path = request.url.path
        assert request.method in {"GET", "POST"}
        if path.endswith("/entity/organization") or path.endswith("/entity/store"):
            return httpx.Response(200, json={"rows": [{"id": str(UUID(int=2))}]})
        if path.endswith(f"/entity/product/{UUID(int=1)}"):
            return httpx.Response(
                200, json={"id": str(UUID(int=1)), "salePrices": [{"value": 100}]}
            )
        if path.endswith("/report/stock/all/current"):
            return httpx.Response(200, json=[{"assortmentId": str(UUID(int=1)), "freeStock": 10}])
        if path.endswith("/entity/counterparty") and request.method == "GET":
            code = request.url.params["filter"].removeprefix("externalCode=")
            return httpx.Response(200, json={"rows": [{"id": buyer_ids[code]}]})
        if path.endswith("/entity/customerorder") and request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": str(UUID(int=10 + len(posted)))})
        raise AssertionError("Unexpected provider effect, including counterparty/history mutation")

    upstream(monkeypatch, provider)
    endpoint = f"/api/runtime/projects/{project}/orders"
    body = order(buyer_name="  Current QA buyer  ", phone=" +7 (900) 000-00-00 ").model_dump(
        mode="json"
    )
    created = await client.post(endpoint, json=body, headers=headers())
    replay = await client.post(endpoint, json=body, headers=headers())
    changed = await client.post(
        endpoint, json={**body, "buyer_name": "Changed QA buyer"}, headers=headers()
    )
    assert created.status_code == replay.status_code == 200
    assert replay.json() == created.json()
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "integration_operation_conflict"
    assert len(posted) == 1
    first = deepcopy(posted[0])
    another = await client.post(
        endpoint,
        json={
            **body,
            "idempotency_key": "another-contact-key",
            "buyer_name": "Next QA recipient",
            "phone": "   ",
        },
        headers=headers(),
    )
    other_actor = await client.post(endpoint, json=body, headers=headers(43))
    assert another.status_code == other_actor.status_code == 200
    assert len(posted) == 3
    assert posted[0] == first
    assert (
        posted[0]["description"] == "Заказ из мини-приложения MAX через Yleum\n"
        "Покупатель: Current QA buyer\nТелефон: +7 (900) 000-00-00"
    )
    assert (
        posted[1]["description"]
        == "Заказ из мини-приложения MAX через Yleum\nПокупатель: Next QA recipient"
    )
    assert posted[0]["agent"] == posted[1]["agent"]
    assert posted[0]["agent"] != posted[2]["agent"]
    assert len({entry["externalCode"] for entry in posted}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phone,digest",
    [(" +7 (900) 000-00-00 ", LEGACY_REQUEST_DIGEST), ("", LEGACY_EMPTY_PHONE_DIGEST)],
)
async def test_http_legacy_raw_receipt_replays_without_provider_and_changed_contact_conflicts(
    client,
    db_session,
    monkeypatch,
    phone,
    digest,
) -> None:
    from sqlalchemy import select

    from tests.test_integration_runtime_contracts import connect, headers, upstream
    from yleum_api.models.app_integration import AccountIntegration
    from yleum_api.models.integration_operation import IntegrationOperation

    project = await connect(client, db_session, monkeypatch, "moysklad")
    connection_id = await db_session.scalar(select(AccountIntegration.id))
    existing_result = {"provider": "moysklad", "id": str(UUID(int=70))}
    db_session.add(
        IntegrationOperation(
            id=uuid4(),
            project_id=UUID(project),
            integration_id=connection_id,
            provider="moysklad",
            max_user_id="42",
            kind="customer_order",
            client_key="one-contact-order-key",
            request_digest=digest,
            status="succeeded",
            result=existing_result,
        )
    )
    await db_session.commit()

    def no_provider(request):
        raise AssertionError("Historical receipt replay/conflict must not preflight or dispatch")

    upstream(monkeypatch, no_provider)
    raw = {
        "idempotency_key": "one-contact-order-key",
        "buyer_name": "  Legacy QA buyer  ",
        "phone": phone,
        "lines": [{"product_id": str(UUID(int=1)), "quantity": "1"}],
    }
    endpoint = f"/api/runtime/projects/{project}/orders"
    replay = await client.post(endpoint, json=raw, headers=headers())
    changed = await client.post(
        endpoint, json={**raw, "buyer_name": "Changed QA buyer"}, headers=headers()
    )
    assert replay.status_code == 200
    assert replay.json() == existing_result
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "integration_operation_conflict"


@pytest.mark.asyncio
async def test_concurrent_order_dispatch_is_single_and_later_replay_keeps_snapshot(
    client,
    db_session,
    monkeypatch,
    test_engine,
) -> None:
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.test_integration_runtime_contracts import connect
    from yleum_api.models.app_integration import AccountIntegration
    from yleum_api.services.integration_operations import execute_once

    project = await connect(client, db_session, monkeypatch, "moysklad")
    connection_id = await db_session.scalar(select(AccountIntegration.id))
    entered, release = asyncio.Event(), asyncio.Event()
    posted = []

    async def provider(request):
        assert request.method == "POST" and request.url.path.endswith("/entity/customerorder")
        posted.append(json.loads(request.content))
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"id": str(UUID(int=10))})

    parsed = order(buyer_name="Concurrent QA buyer", phone="+79000000000")
    prepared = PreparedOrder({}, "stable-actor", str(UUID(int=42)), parsed.buyer_name, parsed.phone)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:

        async def send():
            return await submit_customer_order(provider_client, prepared)

        kwargs = dict(
            project_id=UUID(project),
            integration_id=connection_id,
            max_user_id=42,
            provider="moysklad",
            kind="customer_order",
            client_key=parsed.idempotency_key,
            payload=parsed.model_dump(mode="json"),
            send=send,
        )
        async with factory() as first, factory() as second:
            running = asyncio.create_task(execute_once(first, **kwargs))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                with pytest.raises(ApiError) as duplicate:
                    await execute_once(second, **kwargs)
                assert duplicate.value.code == "integration_operation_unknown"
            finally:
                release.set()
                result = await asyncio.wait_for(running, 3)
        async with factory() as replay_session:
            assert await execute_once(replay_session, **kwargs) == result
    assert len(posted) == 1
    assert (
        posted[0]["description"] == "Заказ из мини-приложения MAX через Yleum\n"
        "Покупатель: Concurrent QA buyer\nТелефон: +79000000000"
    )
