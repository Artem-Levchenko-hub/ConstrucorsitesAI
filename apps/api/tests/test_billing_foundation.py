from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.account_fixture import register_legacy_wallet_account
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.user import User

pytestmark = pytest.mark.asyncio


async def test_public_plan_catalog_is_versioned_and_ordered(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/billing/plans")

    assert response.status_code == 200
    plans = response.json()
    assert [plan["code"] for plan in plans] == ["free", "pro", "business"]
    assert [plan["version"] for plan in plans] == [4, 3, 3]
    assert [plan["price_rub"] for plan in plans] == ["0.00", "2990.00", "6990.00"]
    assert plans[0]["entitlements"]["max_projects"] == 1
    assert plans[0]["entitlements"]["static_publish_slots"] == 1
    assert plans[0]["entitlements"]["included_creations"] == 1
    assert plans[0]["entitlements"]["included_edits"] == 1
    assert plans[0]["entitlements"]["generation_allowance_period"] == "lifetime"
    assert plans[0]["entitlements"]["integrations"] is True
    assert plans[1]["included_credit_rub"] == "0.0000"
    assert plans[1]["entitlements"]["included_creations"] == 2
    assert plans[1]["entitlements"]["included_edits"] == 25
    assert plans[2]["included_credit_rub"] == "0.0000"
    assert plans[2]["entitlements"]["included_creations"] == 5
    assert plans[2]["entitlements"]["included_edits"] == 50
    assert plans[2]["entitlements"]["always_on_slots"] == 1


async def test_historical_paid_plans_keep_original_contract_terms(
    db_session: AsyncSession,
) -> None:
    plans = (
        (await db_session.execute(select(BillingPlan).where(BillingPlan.version == 1)))
        .scalars()
        .all()
    )
    paid = {plan.code: plan for plan in plans if plan.code != "free"}
    assert paid["pro"].price_rub == Decimal("1490.00")
    assert paid["pro"].included_credit_rub == Decimal("500.0000")
    assert paid["business"].price_rub == Decimal("4990.00")
    assert paid["business"].included_credit_rub == Decimal("1500.0000")
    assert all(not plan.is_active for plan in paid.values())


async def test_registration_creates_one_free_subscription(
    client: httpx.AsyncClient,
) -> None:
    registered = await client.post(
        "/api/auth/register",
        json={"email": "subscriber@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    response = await client.get("/api/billing/subscription")
    assert response.status_code == 200
    subscription = response.json()
    assert subscription["status"] == "active"
    assert subscription["cancel_at_period_end"] is False
    assert subscription["plan"]["code"] == "free"
    assert subscription["plan"]["price_rub"] == "0.00"


async def test_database_allows_only_one_live_subscription_per_account(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    await client.post(
        "/api/auth/register",
        json={"email": "one-plan@example.com", "password": "secret123"},
    )
    user_id = (
        await db_session.execute(select(User.id).where(User.email == "one-plan@example.com"))
    ).scalar_one()
    billing_account_id = (
        await db_session.execute(
            select(BillingAccount.id).where(BillingAccount.personal_user_id == user_id)
        )
    ).scalar_one()
    pro_plan_id = (
        await db_session.execute(
            select(BillingPlan.id).where(
                BillingPlan.code == "pro",
                BillingPlan.is_active.is_(True),
            )
        )
    ).scalar_one()

    db_session.add(
        Subscription(
            billing_account_id=billing_account_id,
            user_id=user_id,
            plan_id=pro_plan_id,
            status="active",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_payment_credit_appears_in_canonical_wallet_ledger(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    from yleum_api.routers import payments as payments_router
    from yleum_api.services import yookassa

    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    await register_legacy_wallet_account(client, db_session, "ledger@example.com")

    async def fake_create_payment(**_kwargs: object) -> dict[str, object]:
        return {
            "id": "provider-ledger-payment",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/pay"},
        }

    monkeypatch.setattr(yookassa, "create_payment", fake_create_payment)
    created = await client.post(
        "/api/payments",
        json={
            "package_code": "start",
            "idempotency_key": "a4764c3f-7c21-4c07-8272-bf089d95b8dc",
        },
    )
    assert created.status_code == 201
    payment_id = created.json()["id"]

    async def fake_get_payment(_provider_id: str) -> dict[str, object]:
        return {
            "id": "provider-ledger-payment",
            "status": "succeeded",
            "amount": {"value": "490.00", "currency": "RUB"},
            "metadata": {"payment_id": payment_id},
        }

    monkeypatch.setattr(yookassa, "get_payment", fake_get_payment)
    webhook = await client.post(
        "/api/payments/yookassa/webhook",
        json={"event": "payment.succeeded", "object": {"id": "provider-ledger-payment"}},
    )
    assert webhook.status_code == 200

    wallet = await client.get("/api/wallet")
    assert wallet.status_code == 200
    assert wallet.json()["balance_rub"] == "600.0000"
    [entry] = wallet.json()["recent_charges"]
    assert entry["entry_type"] == "payment"
    assert Decimal(entry["amount_rub"]) == Decimal("500.0000")
    assert Decimal(entry["balance_after_rub"]) == Decimal("600.0000")
    assert entry["external_ref"] == f"payment:{payment_id}"

    exported = await client.get("/api/account/export")
    assert exported.status_code == 200
    assert exported.json()["scope"] == "personal_account_records"
    assert exported.json()["wallet"][0]["balance_rub"] == "600.0000"
    assert exported.json()["transactions"][0]["amount_rub"] == "500.0000"
    assert "provider_payload" not in exported.text and "external_ref" not in exported.text
    subscription = await client.get("/api/billing/subscription")
    assert subscription.status_code == 200
    assert subscription.json()["plan"]["code"] == "free"
    assert subscription.json()["plan"]["version"] == 3
