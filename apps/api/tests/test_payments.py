from __future__ import annotations

import httpx
import pytest

from omnia_api.routers import payments as payments_router
from omnia_api.services import yookassa

pytestmark = pytest.mark.asyncio


async def test_payment_webhook_credits_wallet_exactly_once(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "payer@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    async def fake_create_payment(**_kwargs) -> dict[str, object]:
        return {
            "id": "provider-payment-1",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/pay"},
        }

    monkeypatch.setattr(yookassa, "create_payment", fake_create_payment)
    created = await client.post(
        "/api/payments",
        json={
            "package_code": "start",
            "idempotency_key": "4950cb67-0088-454a-91de-7954fbbaed53",
        },
    )
    assert created.status_code == 201
    payment_id = created.json()["id"]

    async def fake_get_payment(_provider_id: str) -> dict[str, object]:
        return {
            "id": "provider-payment-1",
            "status": "succeeded",
            "amount": {"value": "490.00", "currency": "RUB"},
            "metadata": {"payment_id": payment_id},
        }

    monkeypatch.setattr(yookassa, "get_payment", fake_get_payment)
    payload = {"event": "payment.succeeded", "object": {"id": "provider-payment-1"}}
    first = await client.post("/api/payments/yookassa/webhook", json=payload)
    second = await client.post("/api/payments/yookassa/webhook", json=payload)
    assert first.status_code == 204
    assert second.status_code == 204

    wallet = await client.get("/api/wallet")
    assert wallet.status_code == 200
    assert wallet.json()["balance_rub"] == "600.0000"

    payments = await client.get("/api/payments")
    assert payments.status_code == 200
    assert payments.json()[0]["status"] == "succeeded"
    assert payments.json()[0]["purpose"] == "wallet_topup"


async def test_payment_endpoint_is_closed_without_provider_configuration(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/api/auth/register",
        json={"email": "closed@example.com", "password": "secret123"},
    )
    response = await client.post(
        "/api/payments",
        json={
            "package_code": "start",
            "idempotency_key": "30ae4d20-1a89-44bb-9513-cebd98554866",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "payments_unavailable"
