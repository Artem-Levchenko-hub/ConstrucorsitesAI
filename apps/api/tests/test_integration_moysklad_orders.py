"""Order creation must respect the stock of the selected warehouse."""

import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services.integration_moysklad_orders import (
    PreparedOrder,
    create_customer_order,
    submit_customer_order,
)
from yleum_api.services.integration_operations import SafePreDispatchError


def test_max_order_capability_has_a_runtime_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/unused")
    monkeypatch.setenv("JWT_SECRET", "test-only-012345678901234567890123456789")
    from yleum_api.core.config import get_settings
    from yleum_api.main import create_app

    get_settings.cache_clear()
    try:
        path = "/api/runtime/projects/{project_id}/orders"
        assert "post" in create_app().openapi()["paths"][path]
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected_quantity", "requested_lines"),
    [(0, [Decimal("1")]), (1, [Decimal("1"), Decimal("1")])],
)
async def test_order_rejects_insufficient_selected_store_stock(
    selected_quantity: int, requested_lines: list[Decimal]
) -> None:
    project = UUID("00000000-0000-0000-0000-000000000001")
    organization = UUID("00000000-0000-0000-0000-000000000002")
    store = UUID("00000000-0000-0000-0000-000000000003")
    product = UUID("00000000-0000-0000-0000-000000000004")
    counterparty = UUID("00000000-0000-0000-0000-000000000005")
    order = UUID("00000000-0000-0000-0000-000000000006")
    created_orders = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal created_orders
        path = request.url.path
        if path.endswith(f"/entity/organization/{organization}"):
            return httpx.Response(200, json={"id": str(organization)})
        if path.endswith(f"/entity/store/{store}"):
            return httpx.Response(200, json={"id": str(store)})
        if path.endswith(f"/entity/product/{product}"):
            return httpx.Response(200, json={"id": str(product), "salePrices": [{"value": 10000}]})
        if path.endswith("/report/stock/all/current"):
            # Other stores have stock; only the selected store can fulfill this order.
            assert request.url.params.get("stockType") == "freeStock"
            same_store = f"storeId={store}" in request.url.params.get("filter", "")
            return httpx.Response(
                200,
                json=[
                    {
                        "assortmentId": str(product),
                        "freeStock": selected_quantity if same_store else 5,
                    }
                ],
            )
        if path.endswith("/entity/counterparty") and request.method == "GET":
            return httpx.Response(200, json={"rows": [{"id": str(counterparty)}]})
        if path.endswith("/entity/customerorder") and request.method == "POST":
            created_orders += 1
            return httpx.Response(200, json={"id": str(order)})
        raise AssertionError(f"Unexpected provider call: {request.method} {path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ApiError) as error:
            await create_customer_order(
                client,
                project_id=project,
                max_user_id=123,
                client_key="customer-action-12345",
                buyer_name="Покупатель",
                phone=None,
                lines=[(product, quantity) for quantity in requested_lines],
                organization_id=str(organization),
                store_id=str(store),
            )

    assert error.value.code == "integration_request_rejected"
    assert created_orders == 0


@pytest.mark.asyncio
async def test_order_uses_selected_organization_store_and_reserved_free_stock() -> None:
    project, organization, store, product, buyer, order = [UUID(int=i) for i in range(1, 7)]
    posted: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/entity/organization/{organization}"):
            return httpx.Response(200, json={"id": str(organization)})
        if path.endswith(f"/entity/store/{store}"):
            return httpx.Response(200, json={"id": str(store)})
        if path.endswith(f"/entity/product/{product}"):
            return httpx.Response(200, json={"id": str(product), "salePrices": [{"value": 15000}]})
        if path.endswith("/report/stock/all/current"):
            assert request.url.params.get("stockType") == "freeStock"
            assert f"storeId={store}" in request.url.params.get("filter", "")
            return httpx.Response(200, json=[{"assortmentId": str(product), "freeStock": 2}])
        if path.endswith("/entity/counterparty") and request.method == "GET":
            return httpx.Response(200, json={"rows": [{"id": str(buyer)}]})
        if path.endswith("/entity/customerorder") and request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": str(order)})
        raise AssertionError(f"Unexpected provider call: {request.method} {path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await create_customer_order(
            client,
            project_id=project,
            max_user_id=123,
            client_key="customer-action-12345",
            buyer_name="Покупатель",
            phone=None,
            lines=[(product, Decimal("1"))],
            organization_id=str(organization),
            store_id=str(store),
        )
    assert result["provider"] == "moysklad" and result["id"] == str(order)
    assert Decimal(result["snapshot"]["total_minor"]) == Decimal("15000")
    assert result["snapshot"]["currency"] is None
    assert len(posted) == 1
    assert posted[0]["organization"]["meta"]["href"].endswith(str(organization))
    assert posted[0]["store"]["meta"]["href"].endswith(str(store))


@pytest.mark.asyncio
async def test_stale_preflight_reuses_customer_created_by_another_order() -> None:
    buyer, order = uuid4(), uuid4()
    customer_posts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal customer_posts
        if request.url.path.endswith("/entity/counterparty") and request.method == "GET":
            return httpx.Response(200, json={"rows": [{"id": str(buyer)}]})
        if request.url.path.endswith("/entity/counterparty") and request.method == "POST":
            customer_posts += 1
            return httpx.Response(200, json={"id": str(uuid4())})
        if request.url.path.endswith("/entity/customerorder"):
            return httpx.Response(200, json={"id": str(order)})
        raise AssertionError(f"Unexpected provider call: {request.method} {request.url.path}")

    prepared = PreparedOrder(
        payload={"externalCode": "one-order"},
        customer_code="one-buyer",
        counterparty_id=None,
        buyer_name="Покупатель",
        phone=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await submit_customer_order(client, prepared)
    assert result["id"] == str(order)
    assert customer_posts == 0


@pytest.mark.asyncio
async def test_retry_check_failure_before_post_is_marked_safe() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(503)

    prepared = PreparedOrder(
        payload={"externalCode": "one-order"},
        customer_code="one-buyer",
        counterparty_id=None,
        buyer_name="Покупатель",
        phone=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SafePreDispatchError) as error:
            await submit_customer_order(client, prepared)
    assert error.value.status_code == 503
    assert requests == ["GET"]
