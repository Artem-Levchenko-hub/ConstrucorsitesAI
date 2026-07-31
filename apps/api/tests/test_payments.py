from __future__ import annotations

from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.account import Payment
from omnia_api.models.billing import BillingPlan, Subscription
from omnia_api.models.wallet_charge import WalletCharge
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


async def test_subscription_payment_activates_plan_and_credit_exactly_once(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "subscription@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    provider_calls: list[dict[str, object]] = []

    async def fake_create_payment(**kwargs: object) -> dict[str, object]:
        provider_calls.append(kwargs)
        return {
            "id": "provider-subscription-1",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/subscription"},
        }

    monkeypatch.setattr(yookassa, "create_payment", fake_create_payment)
    idempotency_key = "421ba1e0-9677-4e1f-aee9-80dfd6970e62"
    created = await client.post(
        "/api/payments/subscription",
        json={"plan_code": "pro", "idempotency_key": idempotency_key},
    )
    duplicate = await client.post(
        "/api/payments/subscription",
        json={"plan_code": "pro", "idempotency_key": idempotency_key},
    )
    assert created.status_code == 201
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == created.json()["id"]
    assert len(provider_calls) == 1
    assert provider_calls[0]["amount"] == "1490.00"
    metadata = provider_calls[0]["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["purpose"] == "subscription_initial"
    payment_id = created.json()["id"]

    before_confirmation = await client.get("/api/billing/subscription")
    assert before_confirmation.status_code == 200
    assert before_confirmation.json()["plan"]["code"] == "free"

    async def fake_get_payment(_provider_id: str) -> dict[str, object]:
        return {
            "id": "provider-subscription-1",
            "status": "succeeded",
            "amount": {"value": "1490.00", "currency": "RUB"},
            "metadata": {"payment_id": payment_id},
        }

    monkeypatch.setattr(yookassa, "get_payment", fake_get_payment)
    payload = {
        "event": "payment.succeeded",
        "object": {"id": "provider-subscription-1"},
    }
    first = await client.post("/api/payments/yookassa/webhook", json=payload)
    second = await client.post("/api/payments/yookassa/webhook", json=payload)
    assert first.status_code == 204
    assert second.status_code == 204

    active = await client.get("/api/billing/subscription")
    assert active.status_code == 200
    assert active.json()["plan"]["code"] == "pro"
    assert active.json()["status"] == "active"
    assert active.json()["auto_renew"] is False
    assert active.json()["current_period_start"] is not None
    assert active.json()["current_period_end"] is not None

    wallet = await client.get("/api/wallet")
    assert wallet.status_code == 200
    assert wallet.json()["balance_rub"] == "600.0000"
    subscription_entries = [
        entry
        for entry in wallet.json()["recent_charges"]
        if entry["entry_type"] == "subscription_credit"
    ]
    assert len(subscription_entries) == 1
    assert subscription_entries[0]["external_ref"] == f"subscription-credit:{payment_id}"

    live_count = (
        await db_session.execute(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.status.in_(("trialing", "active", "past_due", "paused")))
        )
    ).scalar_one()
    assert live_count == 1
    plan_codes = list(
        (
            await db_session.execute(
                select(BillingPlan.code, Subscription.status)
                .join(Subscription, Subscription.plan_id == BillingPlan.id)
                .order_by(BillingPlan.sort_order)
            )
        ).all()
    )
    assert plan_codes == [("free", "expired"), ("pro", "active")]
    ledger_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WalletCharge)
            .where(WalletCharge.external_ref == f"subscription-credit:{payment_id}")
        )
    ).scalar_one()
    assert ledger_count == 1
    payment = await db_session.get(Payment, UUID(created.json()["id"]))
    assert payment is not None
    assert payment.purpose == "subscription_initial"
    assert payment.status == "succeeded"
    same_plan = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "idempotency_key": "2a28fcd6-b698-49bc-af68-4fae23025ff4",
        },
    )
    assert same_plan.status_code == 409
    assert same_plan.json()["error"]["code"] == "subscription_already_active"


async def test_only_one_subscription_checkout_can_wait_for_payment(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "one-checkout@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    async def fake_create_payment(**_kwargs: object) -> dict[str, object]:
        return {
            "id": "provider-pending-subscription",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/subscription"},
        }

    monkeypatch.setattr(yookassa, "create_payment", fake_create_payment)
    first = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "idempotency_key": "57a4f44d-3307-4f0b-9cba-76bd2b3b4f2e",
        },
    )
    second = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "business",
            "idempotency_key": "df8fa9a5-2a72-46bd-b4ba-00b5f071239c",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "subscription_checkout_in_progress"


async def test_subscription_checkout_applies_immediate_provider_success(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "immediate-subscription@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    async def fake_create_payment(**kwargs: object) -> dict[str, object]:
        return {
            "id": "provider-immediate-subscription",
            "status": "succeeded",
            "amount": {"value": "4990.00", "currency": "RUB"},
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(yookassa, "create_payment", fake_create_payment)
    created = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "business",
            "idempotency_key": "7cd788e4-672f-4cbc-9306-b00cf17a8492",
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "succeeded"

    subscription = await client.get("/api/billing/subscription")
    assert subscription.status_code == 200
    assert subscription.json()["plan"]["code"] == "business"
    wallet = await client.get("/api/wallet")
    assert wallet.status_code == 200
    assert wallet.json()["balance_rub"] == "1600.0000"
