"""Portable policy checks; row locks and transactions are covered with PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.models.account import Payment
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.user import User
from yleum_api.services import billing_cycle, subscription_email

NOW = datetime(2026, 10, 26, 9, tzinfo=UTC)


def context():
    user = SimpleNamespace(
        id=uuid4(),
        status="active",
        email="owner@example.test",
        is_anon=False,
        role="user",
        email_verified_at=NOW,
        phone_e164=None,
        phone_verified_at=None,
    )
    account = SimpleNamespace(id=uuid4(), scope="personal", personal_user_id=user.id)
    sub = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        billing_account_id=account.id,
        status="active",
        current_period_end=NOW + timedelta(hours=72),
        plan_id=uuid4(),
        auto_renew=False,
        cancel_at_period_end=False,
    )
    payment = SimpleNamespace(
        id=uuid4(),
        status="succeeded",
        refunded_at=None,
        user_id=user.id,
        billing_account_id=account.id,
        subscription_id=sub.id,
    )
    plan = SimpleNamespace(price_rub=Decimal("2990"))
    event = SimpleNamespace(
        event_type="ending_3",
        period_end=sub.current_period_end,
        user_id=user.id,
        billing_account_id=account.id,
        subscription_id=sub.id,
        payment_id=payment.id,
        plan_name="Pro",
        amount_rub=Decimal("2990"),
    )
    objects = {
        User: user,
        BillingAccount: account,
        Subscription: sub,
        Payment: payment,
        BillingPlan: plan,
    }
    session = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, key: objects[model]),
        scalar=AsyncMock(return_value=uuid4()),
    )
    return session, event, objects


@pytest.mark.parametrize("authority", ["email", "phone"])
async def test_optional_email_confirmation_does_not_block_phone_verified_billing(
    monkeypatch,
    authority,
):
    session, event, objects = context()
    user = objects[User]
    if authority == "phone":
        user.email_verified_at, user.phone_verified_at = None, NOW
        user.phone_e164 = "+79990001234"
    monkeypatch.setattr(
        subscription_email, "get_settings", lambda: SimpleNamespace(web_base_url="https://yleum.ru")
    )
    recipient, content = await subscription_email._validated_content(session, event, NOW)
    assert recipient == user.email and "осталось три дня" in content.text
    assert "https://yleum.ru/billing/plan" in content.text
    assert user.email_verified_at == (NOW if authority == "email" else None)


@pytest.mark.parametrize("phone", ["none", "timestamp_only", "number_only"])
async def test_legacy_timestamp_without_canonical_authority_cannot_receive_mail(monkeypatch, phone):
    session, event, objects = context()
    session.scalar.return_value = None
    user = objects[User]
    if phone == "timestamp_only":
        user.phone_verified_at = NOW
    elif phone == "number_only":
        user.phone_e164 = "+79990001234"
    monkeypatch.setattr(
        subscription_email, "get_settings", lambda: SimpleNamespace(web_base_url="https://yleum.ru")
    )
    assert await subscription_email._validated_content(session, event, NOW) is None


async def test_paid_receipt_copy_remains_true_after_subscription_expires(monkeypatch):
    session, event, objects = context()
    event.event_type = "paid"
    objects[Subscription].status = "expired"
    monkeypatch.setattr(
        subscription_email, "get_settings", lambda: SimpleNamespace(web_base_url="https://yleum.ru")
    )
    _, content = await subscription_email._validated_content(session, event, NOW)
    assert "Оплата подписки Pro подтверждена" in content.text
    assert "подписка активна" not in content.html.casefold()
    assert "подписка Pro активна" not in content.text


@pytest.mark.parametrize(
    "change", ["unverified", "blocked", "owner", "refunded", "renewed", "expired", "free", "stale"]
)
async def test_ineligible_reminders_are_suppressed(monkeypatch, change):
    session, event, objects = context()
    if change == "unverified":
        objects[User].email_verified_at = None
    elif change == "blocked":
        objects[User].status = "blocked"
    elif change == "owner":
        objects[BillingAccount].personal_user_id = uuid4()
    elif change == "refunded":
        objects[Payment].refunded_at = NOW
    elif change == "renewed":
        objects[Subscription].current_period_end += timedelta(days=30)
    elif change == "expired":
        objects[Subscription].status = "expired"
    elif change == "free":
        objects[BillingPlan].price_rub = Decimal("0")
    monkeypatch.setattr(
        subscription_email, "get_settings", lambda: SimpleNamespace(web_base_url="https://yleum.ru")
    )
    when = NOW + timedelta(hours=30) if change == "stale" else NOW
    assert await subscription_email._validated_content(session, event, when) is None


async def test_billing_health_loop_runs_while_email_sender_is_blocked_and_cancels_it(monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()
    engine = SimpleNamespace(dispose=AsyncMock())
    state = billing_cycle.BillingCycleState(poll_seconds=10)
    factory = object()
    settings = SimpleNamespace(
        database_url="never-connected",
        billing_lifecycle_poll_seconds=10,
        billing_renewal_retry_hours=24,
        billing_grace_days=3,
        billing_payment_reconcile_after_minutes=5,
    )

    async def email_loop(received_factory, **kwargs):
        assert received_factory is factory
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def billing_loop(*args, **kwargs):
        await asyncio.wait_for(started.wait(), timeout=1)
        state.succeed(NOW, subscriptions=1, payments=2)

    monkeypatch.setattr(billing_cycle, "get_settings", lambda: settings)
    monkeypatch.setattr(billing_cycle, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(billing_cycle, "async_sessionmaker", lambda *args, **kwargs: factory)
    monkeypatch.setattr(billing_cycle, "run_subscription_email_loop", email_loop)
    monkeypatch.setattr(billing_cycle, "run_billing_loop", billing_loop)
    await billing_cycle.run_billing_cycles_forever(state=state)
    assert state.healthy(NOW) and state.processed_subscriptions == 1
    assert cancelled.is_set()
    engine.dispose.assert_awaited_once()
