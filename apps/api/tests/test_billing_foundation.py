from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.account import BusinessEntitlement
from omnia_api.models.billing import BillingAccount, BillingPlan, Subscription
from omnia_api.models.user import User
from omnia_api.models.wallet_charge import WalletCharge
from omnia_api.services.billing import topup

pytestmark = pytest.mark.asyncio


async def test_public_plan_catalog_is_versioned_and_ordered(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/billing/plans")

    assert response.status_code == 200
    plans = response.json()
    assert [plan["code"] for plan in plans] == ["free", "pro", "business"]
    assert [plan["version"] for plan in plans] == [1, 1, 1]
    assert [plan["price_rub"] for plan in plans] == ["0.00", "1490.00", "4990.00"]
    assert plans[1]["included_credit_rub"] == "500.0000"
    assert plans[2]["entitlements"]["always_on_slots"] == 1


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


async def test_max_business_keeps_the_same_account_and_financial_history(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import max_accounts as max_accounts_router

    async def verified_npd(_inn: str) -> tuple[str, str | None, dict[str, object]]:
        return "verified", None, {"status": True}

    monkeypatch.setattr(max_accounts_router, "_verify_self_employed", verified_npd)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "business-ledger@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201
    user_id = (
        await db_session.execute(select(User.id).where(User.email == "business-ledger@example.com"))
    ).scalar_one()
    original = (
        await db_session.execute(
            select(BillingAccount).where(BillingAccount.personal_user_id == user_id)
        )
    ).scalar_one()
    original_id = original.id
    new_balance = await topup(
        db_session,
        user_id,
        Decimal("25.0000"),
        "Balance created before business onboarding",
    )
    assert new_balance == Decimal("125.0000")
    await db_session.commit()

    business = await client.put(
        "/api/max/account/business",
        json={
            "kind": "self_employed",
            "inn": "500100732259",
            "legal_name": "Иванов Иван Иванович",
        },
    )
    assert business.status_code == 200

    promoted = await db_session.get(BillingAccount, original_id)
    assert promoted is not None
    assert promoted.scope == "business"
    assert promoted.personal_user_id is None
    assert str(promoted.business_id) == business.json()["id"]

    entitlement = await db_session.get(BusinessEntitlement, promoted.business_id)
    assert entitlement is not None
    entitlement.free_generations_used = 2
    await db_session.commit()

    subscription = await client.get("/api/billing/subscription")
    assert subscription.status_code == 200
    assert subscription.json()["plan"]["code"] == "free"
    wallet = await client.get("/api/wallet")
    assert wallet.status_code == 200
    assert wallet.json()["balance_rub"] == "125.0000"
    assert wallet.json()["free_generations_left"] == 1
    assert wallet.json()["free_generation_limit"] == 3
    [ledger_entry] = (
        (
            await db_session.execute(
                select(WalletCharge).where(
                    WalletCharge.description == "Balance created before business onboarding"
                )
            )
        )
        .scalars()
        .all()
    )
    assert ledger_entry.billing_account_id == original_id
    assert wallet.json()["recent_charges"][0]["description"] == ledger_entry.description


async def test_payment_credit_appears_in_canonical_wallet_ledger(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import payments as payments_router
    from omnia_api.services import yookassa

    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "ledger@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

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
    assert webhook.status_code == 204

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
    [exported_subscription] = exported.json()["subscriptions"]
    assert exported_subscription["plan"]["code"] == "free"
    assert exported_subscription["plan"]["version"] == 1
    [exported_entry] = exported.json()["wallet_ledger"]
    assert exported_entry["type"] == "payment"
    assert exported_entry["external_ref"] == f"payment:{payment_id}"
