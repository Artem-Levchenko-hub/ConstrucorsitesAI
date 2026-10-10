"""Submitted price snapshots must come from provider preflight, never the browser."""

import json
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from yleum_api.services.integration_moysklad_orders import create_customer_order


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_sum", [None, 74750, "74750", True, "NaN"])
@pytest.mark.parametrize("document_currency", [None, "RUB", "USD", "invalid", 643])
async def test_submitted_snapshot_is_exact_and_missing_provider_total_is_explicit(
    provider_sum, document_currency
):
    organization, store, product, buyer, order, currency = [UUID(int=i) for i in range(1, 7)]
    posted = []

    def respond(request):
        path = request.url.path
        if path.endswith("/entity/organization") or path.endswith("/entity/store"):
            return httpx.Response(
                200,
                json={
                    "rows": [{"id": str(organization if path.endswith("organization") else store)}]
                },
            )
        if path.endswith(f"/entity/product/{product}"):
            return httpx.Response(
                200,
                json={
                    "id": str(product),
                    "name": "QA product",
                    "salePrices": [
                        {
                            "value": 29900,
                            "currency": {
                                "isoCode": "USD",
                                "meta": {
                                    "href": f"https://api.moysklad.ru/api/remap/1.2/entity/currency/{currency}"
                                },
                            },
                        }
                    ],
                },
            )
        if path.endswith(f"/entity/currency/{currency}"):
            return httpx.Response(200, json={"id": str(currency), "isoCode": "RUB"})
        if path.endswith("/report/stock/all/current"):
            return httpx.Response(200, json=[{"assortmentId": str(product), "freeStock": 10}])
        if path.endswith("/entity/counterparty"):
            return httpx.Response(200, json={"rows": [{"id": str(buyer)}]})
        if path.endswith("/entity/customerorder"):
            posted.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": str(order),
                    **({"sum": provider_sum} if provider_sum is not None else {}),
                    **(
                        {"rate": {"currency": {"isoCode": document_currency}}}
                        if document_currency is not None
                        else {}
                    ),
                },
            )
        raise AssertionError(f"Unexpected read/write: {request.method} {path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await create_customer_order(
            client,
            project_id=UUID(int=90),
            max_user_id=42,
            client_key="qa-managed-order-intent",
            buyer_name="QA buyer",
            phone=None,
            lines=[(product, Decimal("1")), (product, Decimal("1.5"))],
        )
    snapshot = result.get("snapshot")
    assert snapshot is not None
    assert snapshot["amount_source"] == "server_submitted"
    expected_currency = document_currency if document_currency in ("RUB", "USD") else None
    assert snapshot["currency"] == expected_currency
    assert result.get("provider_total_currency") == expected_currency
    assert Decimal(snapshot["total_minor"]) == Decimal("74750")
    assert snapshot["lines"] == [
        {
            "product_id": str(product),
            "name": "QA product",
            "quantity": "2.5",
            "unit_price_minor": "29900.0",
            "total_minor": "74750.00",
        }
    ]
    assert "QA buyer" not in json.dumps(snapshot)
    assert result.get("provider_total_minor") == (
        "74750" if provider_sum in (74750, "74750") else None
    )
    assert len(posted) == 1 and posted[0]["positions"][0]["price"] == 29900
    assert "rate" not in posted[0]
