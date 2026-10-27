"""Fast, bounded outbox recovery without replaying ambiguous SMTP acceptance."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_subscription_email_events import NOW, setup_paid
from yleum_api.models.billing import BillingPlan
from yleum_api.models.email_notification import EmailNotification
from yleum_api.services import subscription_email as service
from yleum_api.services.transactional_email import EmailDeliveryFailed, EmailDeliveryNotConfigured


@pytest.fixture
async def pending_mail(db_session):
    _, sub, payment = await setup_paid(db_session)
    await service.enqueue_paid_email(
        db_session, payment, sub, await db_session.get(BillingPlan, sub.plan_id)
    )
    await db_session.commit()
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


async def event(factory):
    async with factory() as session:
        return await session.scalar(select(EmailNotification))


def clock(monkeypatch):
    state = SimpleNamespace(seconds=0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=state.seconds)

    monkeypatch.setattr(service, "datetime", Clock)
    # Patch the module reference, never the process-wide asyncio/time modules.
    monkeypatch.setattr(
        service, "time", SimpleNamespace(monotonic=lambda: state.seconds), raising=False
    )
    return state


async def test_loop_recovers_disconnected_mail_in_three_seconds_with_billing_poll_sixty(
    pending_mail, monkeypatch
):
    timer = clock(monkeypatch)
    calls = []

    async def send(**kwargs):
        calls.append((timer.seconds, kwargs["message_id"]))
        if len(calls) == 1:
            raise EmailDeliveryFailed("disconnected", retry_safe=True, category="disconnected")

    async def sleep(seconds):
        if len(calls) == 2:
            raise asyncio.CancelledError
        assert seconds <= 3
        timer.seconds += seconds

    reminders = AsyncMock()
    monkeypatch.setattr(service, "enqueue_reminders", reminders)
    monkeypatch.setattr(service, "send_transactional_email", send)
    monkeypatch.setattr(service, "asyncio", SimpleNamespace(sleep=sleep))
    with pytest.raises(asyncio.CancelledError):
        await service.run_subscription_email_loop(pending_mail, poll_seconds=60)
    row = await event(pending_mail)
    assert calls == [(0, row.id), (3, row.id)]
    assert row.status == "sent" and row.attempts == 2
    reminders.assert_awaited_once()


@pytest.mark.parametrize("category", ["disconnected", "timeout", "network", "connection"])
async def test_transient_retries_are_due_once_and_back_off_after_three_claims(
    pending_mail, monkeypatch, category
):
    calls = []

    async def send(**kwargs):
        calls.append(kwargs["message_id"])
        raise EmailDeliveryFailed("temporary failure", retry_safe=True, category=category)

    monkeypatch.setattr(service, "send_transactional_email", send)
    current = NOW
    for attempt, delay in enumerate((3, 3, 3, 60, 60), start=1):
        results = await asyncio.gather(
            service.deliver_one(pending_mail, now=current),
            service.deliver_one(pending_mail, now=current),
        )
        assert sorted(results) == [False, True]
        row = await event(pending_mail)
        assert row.status == "pending" and row.attempts == attempt
        assert row.available_at == current + timedelta(seconds=delay)
        assert not await service.deliver_one(
            pending_mail, now=row.available_at - timedelta(microseconds=1)
        )
        current = row.available_at
    assert len(calls) == 5 and len(set(calls)) == 1


@pytest.mark.parametrize("category", ["authentication", "tls_certificate", "not_configured"])
async def test_configuration_failure_keeps_conservative_retry(pending_mail, monkeypatch, category):
    async def send(**kwargs):
        if category == "not_configured":
            raise EmailDeliveryNotConfigured("not configured")
        raise EmailDeliveryFailed("configuration failure", retry_safe=True, category=category)

    monkeypatch.setattr(service, "send_transactional_email", send)
    assert await service.deliver_one(pending_mail, now=NOW)
    row = await event(pending_mail)
    assert row.available_at == NOW + timedelta(minutes=5)
    assert not await service.deliver_one(pending_mail, now=NOW + timedelta(seconds=60))


async def test_ambiguous_disconnect_never_retries_fast_or_later(pending_mail, monkeypatch):
    sender = AsyncMock(side_effect=EmailDeliveryFailed(
        "acceptance unknown", retry_safe=False, category="disconnected"
    ))
    monkeypatch.setattr(service, "send_transactional_email", sender)
    assert await service.deliver_one(pending_mail, now=NOW)
    assert not await service.deliver_one(pending_mail, now=NOW + timedelta(seconds=3))
    assert not await service.deliver_one(pending_mail, now=NOW + timedelta(days=1))
    row = await event(pending_mail)
    assert row.status == "uncertain" and row.attempts == 1
    sender.assert_awaited_once()


@pytest.mark.parametrize("explicit_now", [False, True])
async def test_retry_deadline_uses_failure_completion_unless_now_is_explicit(
    pending_mail, monkeypatch, explicit_now
):
    timer = clock(monkeypatch)

    async def send(**kwargs):
        timer.seconds += 15
        raise EmailDeliveryFailed("timeout", retry_safe=True, category="timeout")

    monkeypatch.setattr(service, "send_transactional_email", send)
    await service.deliver_one(pending_mail, now=NOW if explicit_now else None)
    row = await event(pending_mail)
    assert row.available_at == NOW + timedelta(seconds=3 if explicit_now else 18)


@pytest.mark.parametrize("reminder_fails", [False, True])
async def test_reminders_keep_sixty_second_cadence_without_blocking_delivery(
    monkeypatch, reminder_fails
):
    timer = clock(monkeypatch)
    reminders, deliveries = [], []

    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(begin=transaction)

    async def discover(session):
        reminders.append(timer.seconds)
        if reminder_fails:
            raise RuntimeError("reminder discovery unavailable")

    async def deliver(factory):
        deliveries.append(timer.seconds)
        return False

    async def sleep(seconds):
        assert seconds <= 3
        timer.seconds += seconds
        if timer.seconds > 60:
            raise asyncio.CancelledError

    monkeypatch.setattr(service, "enqueue_reminders", discover)
    monkeypatch.setattr(service, "deliver_one", deliver)
    monkeypatch.setattr(service, "asyncio", SimpleNamespace(sleep=sleep))
    with pytest.raises(asyncio.CancelledError):
        await service.run_subscription_email_loop(factory, poll_seconds=60)
    assert reminders == [0, 60]
    assert deliveries == list(range(0, 61, 3))
