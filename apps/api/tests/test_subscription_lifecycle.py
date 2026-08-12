from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.models.account import Payment
from omnia_api.models.billing import (
    BUSINESS_PLAN_ID,
    PRO_PLAN_ID,
    BillingAccount,
    BillingPaymentMethod,
    Subscription,
)
from omnia_api.models.project import Project
from omnia_api.models.user import User
from omnia_api.models.wallet_charge import WalletCharge
from omnia_api.routers import payments as payments_router
from omnia_api.services import orchestrator_client, yookassa
from omnia_api.services.subscription_lifecycle import process_subscription_cycle

pytestmark = pytest.mark.asyncio


async def _registered_billing_context(
    client: httpx.AsyncClient,
    session: AsyncSession,
    *,
    email: str,
) -> tuple[User, BillingAccount, Subscription]:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123"},
    )
    assert response.status_code == 201
    user = (await session.execute(select(User).where(User.email == email))).scalar_one()
    account = (
        await session.execute(
            select(BillingAccount).where(BillingAccount.personal_user_id == user.id)
        )
    ).scalar_one()
    free = (
        await session.execute(
            select(Subscription).where(Subscription.billing_account_id == account.id)
        )
    ).scalar_one()
    return user, account, free


async def test_auto_renew_requires_consent_and_can_be_cancelled_then_restored(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    await _registered_billing_context(
        client,
        db_session,
        email="renewal-consent@example.com",
    )
    rejected = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "idempotency_key": "cc65869c-5717-4a88-a678-f70aa5ef97e3",
            "auto_renew": True,
            "consent_version": "outdated",
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "subscription_consent_required"

    create_calls: list[dict[str, object]] = []

    async def fake_create_payment(**kwargs: object) -> dict[str, object]:
        create_calls.append(kwargs)
        return {
            "id": "provider-saved-method",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/pay"},
        }

    monkeypatch.setattr(yookassa, "create_payment", fake_create_payment)
    created = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "idempotency_key": "bc143ea1-34c0-4564-bf22-7986f06483df",
            "auto_renew": True,
            "consent_version": get_settings().legal_document_version,
        },
    )
    assert created.status_code == 201
    assert create_calls[0]["save_payment_method"] is True
    payment_id = created.json()["id"]

    async def fake_get_payment(_provider_id: str) -> dict[str, object]:
        return {
            "id": "provider-saved-method",
            "status": "succeeded",
            "amount": {"value": "1490.00", "currency": "RUB"},
            "metadata": {"payment_id": payment_id},
            "payment_method": {
                "id": "saved-method-1",
                "saved": True,
                "title": "Банковская карта",
                "card": {"last4": "4242"},
            },
        }

    monkeypatch.setattr(yookassa, "get_payment", fake_get_payment)
    webhook = await client.post(
        "/api/payments/yookassa/webhook",
        json={"object": {"id": "provider-saved-method"}},
    )
    assert webhook.status_code == 204
    active = await client.get("/api/billing/subscription")
    assert active.json()["auto_renew"] is True
    assert active.json()["next_charge_at"] == active.json()["current_period_end"]

    canceled = await client.patch(
        "/api/billing/subscription",
        json={"action": "cancel"},
    )
    assert canceled.status_code == 200
    assert canceled.json()["auto_renew"] is False
    assert canceled.json()["cancel_at_period_end"] is True
    assert canceled.json()["can_restore"] is True

    restored = await client.patch(
        "/api/billing/subscription",
        json={
            "action": "restore",
            "consent_version": get_settings().legal_document_version,
        },
    )
    assert restored.status_code == 200
    assert restored.json()["auto_renew"] is True
    assert restored.json()["cancel_at_period_end"] is False
    assert restored.json()["next_charge_at"] == restored.json()["current_period_end"]


async def test_lifetime_business_is_visible_and_cannot_be_replaced_or_downgraded(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, account, free = await _registered_billing_context(
        client,
        db_session,
        email="lifetime-business@example.com",
    )
    now = datetime.now(UTC).replace(microsecond=0)
    free.status = "expired"
    free.ended_at = now
    lifetime = Subscription(
        billing_account_id=account.id,
        user_id=user.id,
        plan_id=BUSINESS_PLAN_ID,
        status="active",
        is_lifetime=True,
        auto_renew=False,
        cancel_at_period_end=False,
        current_period_start=now,
    )
    db_session.add(lifetime)
    await db_session.commit()

    current = await client.get("/api/billing/subscription")
    assert current.status_code == 200
    assert current.json()["plan"]["code"] == "business"
    assert current.json()["is_lifetime"] is True
    assert current.json()["current_period_end"] is None
    assert current.json()["next_charge_at"] is None

    manage = await client.patch("/api/billing/subscription", json={"action": "cancel"})
    assert manage.status_code == 409
    assert manage.json()["error"]["code"] == "subscription_management_unavailable"

    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    checkout = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "idempotency_key": "f15c31aa-f218-41a9-b41f-dd68b49b378a",
            "auto_renew": False,
        },
    )
    assert checkout.status_code == 409
    assert checkout.json()["error"]["code"] == "subscription_already_active"

    processed = await process_subscription_cycle(
        db_session,
        now=now + timedelta(days=36500),
    )
    assert processed == 0
    await db_session.refresh(lifetime)
    assert lifetime.status == "active"
    assert lifetime.is_lifetime is True


async def test_lifecycle_renews_and_credits_exactly_once(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, account, free = await _registered_billing_context(
        client,
        db_session,
        email="renewal-success@example.com",
    )
    now = datetime.now(UTC).replace(microsecond=0)
    free.status = "expired"
    free.ended_at = now
    await db_session.flush()
    method = BillingPaymentMethod(
        billing_account_id=account.id,
        user_id=user.id,
        provider="yookassa",
        provider_payment_method_id="recurring-method-success",
        status="active",
        consent_version=get_settings().legal_document_version,
        consented_at=now,
    )
    db_session.add(method)
    await db_session.flush()
    subscription = Subscription(
        billing_account_id=account.id,
        user_id=user.id,
        plan_id=PRO_PLAN_ID,
        payment_method_id=method.id,
        status="active",
        auto_renew=True,
        current_period_start=now - timedelta(days=30),
        current_period_end=now,
        next_charge_at=now,
        renewal_consent_version=get_settings().legal_document_version,
        renewal_consented_at=now,
    )
    db_session.add(subscription)
    await db_session.commit()

    provider_calls = 0

    async def fake_recurring(**kwargs: object) -> dict[str, object]:
        nonlocal provider_calls
        provider_calls += 1
        return {
            "id": "provider-renewal-success",
            "status": "succeeded",
            "amount": {"value": "1490.00", "currency": "RUB"},
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(yookassa, "create_recurring_payment", fake_recurring)
    assert await process_subscription_cycle(db_session, now=now) == 1
    assert await process_subscription_cycle(db_session, now=now) == 0
    await db_session.refresh(subscription)
    assert subscription.status == "active"
    assert subscription.current_period_start == now
    assert subscription.current_period_end is not None
    assert subscription.current_period_end > now
    assert provider_calls == 1
    renewal_count = (
        await db_session.scalar(
            select(func.count())
            .select_from(Payment)
            .where(Payment.purpose == "subscription_renewal")
        )
        or 0
    )
    credit_count = (
        await db_session.scalar(
            select(func.count())
            .select_from(WalletCharge)
            .where(WalletCharge.subscription_id == subscription.id)
        )
        or 0
    )
    assert renewal_count == 1
    assert credit_count == 1


async def test_failed_renewal_grace_then_downgrades_and_revokes_keep_alive(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, account, free = await _registered_billing_context(
        client,
        db_session,
        email="renewal-failure@example.com",
    )
    now = datetime.now(UTC).replace(microsecond=0)
    free.status = "expired"
    free.ended_at = now
    await db_session.flush()
    method = BillingPaymentMethod(
        billing_account_id=account.id,
        user_id=user.id,
        provider="yookassa",
        provider_payment_method_id="recurring-method-failure",
        status="active",
        consent_version=get_settings().legal_document_version,
        consented_at=now,
    )
    db_session.add(method)
    await db_session.flush()
    subscription = Subscription(
        billing_account_id=account.id,
        user_id=user.id,
        plan_id=PRO_PLAN_ID,
        payment_method_id=method.id,
        status="active",
        auto_renew=True,
        current_period_start=now - timedelta(days=30),
        current_period_end=now,
        next_charge_at=now,
        renewal_consent_version=get_settings().legal_document_version,
        renewal_consented_at=now,
    )
    project = Project(
        owner_id=user.id,
        name="Always on app",
        slug="always-on-renewal-test",
        template="max_miniapp",
        keep_alive_enabled=True,
    )
    db_session.add_all([subscription, project])
    await db_session.commit()

    async def provider_unavailable(**_kwargs: object) -> dict[str, object]:
        raise yookassa.YooKassaUnavailable("offline")

    revoked: list[object] = []

    async def fake_keep_alive(project_id, *, enabled: bool):
        revoked.append((project_id, enabled))
        return {"project_id": str(project_id), "enabled": enabled}

    monkeypatch.setattr(yookassa, "create_recurring_payment", provider_unavailable)
    monkeypatch.setattr(orchestrator_client, "set_keep_alive", fake_keep_alive)
    assert await process_subscription_cycle(db_session, now=now) == 1
    await db_session.refresh(subscription)
    assert subscription.status == "past_due"
    assert subscription.grace_period_ends_at == now + timedelta(
        days=get_settings().billing_grace_days
    )

    after_grace = subscription.grace_period_ends_at + timedelta(seconds=1)
    assert await process_subscription_cycle(db_session, now=after_grace) == 1
    await db_session.refresh(subscription)
    await db_session.refresh(project)
    assert subscription.status == "expired"
    assert project.keep_alive_enabled is False
    assert revoked == [(project.id, False)]
    live = list(
        (
            await db_session.execute(
                select(Subscription).where(
                    Subscription.billing_account_id == account.id,
                    Subscription.status.in_(("trialing", "active", "past_due", "paused")),
                )
            )
        ).scalars()
    )
    assert len(live) == 1
    assert live[0].plan_id != PRO_PLAN_ID


async def test_business_keep_alive_entitlement_enforces_one_slot(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, account, free = await _registered_billing_context(
        client,
        db_session,
        email="always-on-slot@example.com",
    )
    first = Project(
        owner_id=user.id,
        name="First runtime",
        slug="first-runtime-slot",
        template="blank",
    )
    second = Project(
        owner_id=user.id,
        name="Second runtime",
        slug="second-runtime-slot",
        template="blank",
    )
    db_session.add_all([first, second])
    await db_session.commit()

    free_denied = await client.post(
        f"/api/projects/{first.id}/runtime/keep-alive",
        json={"enabled": True},
    )
    assert free_denied.status_code == 402
    assert free_denied.json()["error"]["code"] == "subscription_entitlement_required"

    free.status = "expired"
    free.ended_at = datetime.now(UTC)
    await db_session.flush()
    db_session.add(
        Subscription(
            billing_account_id=account.id,
            user_id=user.id,
            plan_id=BUSINESS_PLAN_ID,
            status="active",
            auto_renew=False,
            current_period_start=datetime.now(UTC),
            current_period_end=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await db_session.commit()

    async def fake_provision(**kwargs: object) -> dict[str, object]:
        return {
            "state": "running",
            "container_name": f"omnia-dev-{kwargs['slug']}",
            "port": 3200,
            "dev_url": "https://preview.test",
        }

    async def fake_keep_alive(project_id, *, enabled: bool):
        return {"project_id": str(project_id), "enabled": enabled}

    monkeypatch.setattr(orchestrator_client, "provision", fake_provision)
    monkeypatch.setattr(orchestrator_client, "set_keep_alive", fake_keep_alive)
    enabled = await client.post(
        f"/api/projects/{first.id}/runtime/keep-alive",
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["keep_alive"] is True
    await db_session.refresh(first)
    assert first.keep_alive_enabled is True

    full = await client.post(
        f"/api/projects/{second.id}/runtime/keep-alive",
        json={"enabled": True},
    )
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "subscription_entitlement_required"
