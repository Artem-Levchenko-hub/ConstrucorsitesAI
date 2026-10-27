from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.models.account import Payment
from yleum_api.models.billing import PRO_PLAN_V3_ID, BillingAccount, BillingPlan, Subscription
from yleum_api.models.user import User
from yleum_api.services import transactional_email

NOW = datetime(2026, 10, 26, 9, tzinfo=UTC)


async def setup_paid(session, *, hours=72):
    user = User(
        email=f"{uuid4()}@example.test",
        status="active",
        phone_e164="+79" + str(uuid4().int % 10**9).zfill(9),
        phone_verified_at=NOW,
    )
    session.add(user)
    await session.flush()
    account = BillingAccount(personal_user_id=user.id)
    session.add(account)
    await session.flush()
    sub = Subscription(
        user_id=user.id,
        billing_account_id=account.id,
        # This fixture deliberately represents the accepted one-ruble contract.
        # New catalog prices must not rewrite its payment or notification terms.
        plan_id=PRO_PLAN_V3_ID,
        status="active",
        auto_renew=False,
        current_period_start=NOW - timedelta(days=27),
        current_period_end=NOW + timedelta(hours=hours),
    )
    session.add(sub)
    await session.flush()
    payment = Payment(
        user_id=user.id,
        billing_account_id=account.id,
        subscription_id=sub.id,
        idempotency_key=str(uuid4()),
        purpose="subscription_initial",
        package_code="pro",
        amount_rub=Decimal("1.00"),
        credit_rub=Decimal("0"),
        status="succeeded",
        paid_at=NOW,
    )
    session.add(payment)
    await session.flush()
    return user, sub, payment


async def test_payment_event_unique_and_transactional(db_session):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services.subscription_email import enqueue_paid_email

    _, sub, payment = await setup_paid(db_session)
    plan = await db_session.get(BillingPlan, sub.plan_id)
    await enqueue_paid_email(db_session, payment, sub, plan)
    await enqueue_paid_email(db_session, payment, sub, plan)
    assert await db_session.scalar(select(func.count()).select_from(EmailNotification)) == 1
    await db_session.rollback()
    assert await db_session.scalar(select(func.count()).select_from(EmailNotification)) == 0


@pytest.mark.parametrize(
    "hours,expected",
    [(73, None), (72, 3), (49, 3), (48, None), (25, None), (24, 1), (1, 1), (0, None)],
)
async def test_reminder_windows_and_repeated_ticks(db_session, hours, expected):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services.subscription_email import enqueue_reminders

    await setup_paid(db_session, hours=hours)
    await db_session.commit()
    await enqueue_reminders(db_session, now=NOW)
    await enqueue_reminders(db_session, now=NOW)
    rows = (await db_session.scalars(select(EmailNotification))).all()
    assert len(rows) == (0 if expected is None else 1)
    if expected:
        assert rows[0].event_type == f"ending_{expected}"


async def test_stale_period_suppressed_and_next_period_can_notify(db_session):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services.subscription_email import deliver_one, enqueue_reminders

    _, sub, _ = await setup_paid(db_session)
    await db_session.commit()
    await enqueue_reminders(db_session, now=NOW)
    await db_session.commit()
    sub.current_period_end += timedelta(days=30)
    await db_session.commit()
    assert await deliver_one(async_sessionmaker(db_session.bind, expire_on_commit=False), now=NOW)
    row = await db_session.scalar(select(EmailNotification))
    await db_session.refresh(row)
    assert row.status == "skipped"
    await enqueue_reminders(db_session, now=NOW + timedelta(days=30))
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(EmailNotification)) == 2


async def test_delivery_claim_is_committed_before_smtp_and_never_replays_uncertain(
    db_session, monkeypatch
):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services import subscription_email as service

    _, sub, payment = await setup_paid(db_session)
    await service.enqueue_paid_email(
        db_session, payment, sub, await db_session.get(BillingPlan, sub.plan_id)
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    calls = []

    async def send(**kwargs):
        async with factory() as other:
            assert await other.scalar(select(EmailNotification.status)) == "sending"
        assert not await service.deliver_one(factory, now=NOW)
        calls.append(kwargs)
        raise transactional_email.EmailDeliveryFailed("SMTP delivery failed", retry_safe=False)

    monkeypatch.setattr(service, "send_transactional_email", send)
    assert await service.deliver_one(factory, now=NOW)
    assert not await service.deliver_one(factory, now=NOW + timedelta(hours=1))
    async with factory() as other:
        row = await other.scalar(select(EmailNotification))
        assert row.status == "uncertain" and row.attempts == 1
        assert calls[0]["message_id"] == row.id
    assert len(calls) == 1


async def test_definitive_smtp_failure_retries_then_marks_sent(db_session, monkeypatch):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services import subscription_email as service

    await setup_paid(db_session)
    await db_session.commit()
    await service.enqueue_reminders(db_session, now=NOW)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def fail(**kwargs):
        raise transactional_email.EmailDeliveryFailed(
            "SMTP delivery failed", retry_safe=True, category="connection"
        )

    monkeypatch.setattr(service, "send_transactional_email", fail)
    await service.deliver_one(factory, now=NOW)
    async with factory() as other:
        row = await other.scalar(select(EmailNotification))
        assert row.status == "pending"

    async def success(**kwargs):
        pass

    monkeypatch.setattr(service, "send_transactional_email", success)
    await service.deliver_one(factory, now=NOW + timedelta(minutes=6))
    async with factory() as other:
        row = await other.scalar(select(EmailNotification))
        assert row.status == "sent" and row.attempts == 2


@pytest.mark.parametrize("purpose", ["subscription_initial", "subscription_renewal"])
async def test_verified_success_webhook_queues_once_without_smtp(
    client,
    db_session,
    monkeypatch,
    purpose,
):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services import subscription_email, yookassa

    _, sub, payment = await setup_paid(db_session)
    if purpose == "subscription_initial":
        sub.status = "pending_payment"
    payment.purpose = purpose
    payment.status = "pending"
    payment.paid_at = None
    payment.provider_payment_id = f"provider-{uuid4()}"
    provider = {
        "id": payment.provider_payment_id,
        "status": "succeeded",
        "amount": {"currency": "RUB", "value": "1.00"},
        "metadata": {"payment_id": str(payment.id)},
    }
    await db_session.commit()

    async def fetch(_provider_id):
        return provider

    async def no_inline_smtp(**kwargs):
        pytest.fail("payment webhook must only enqueue mail")

    monkeypatch.setattr(yookassa, "get_payment", fetch)
    monkeypatch.setattr(yookassa, "notification_source_allowed", lambda _: True)
    monkeypatch.setattr(subscription_email, "send_transactional_email", no_inline_smtp)
    for _ in range(2):
        result = await client.post(
            "/api/payments/yookassa/webhook", json={"object": {"id": payment.provider_payment_id}}
        )
        assert result.status_code == 200, result.text
    await db_session.refresh(payment)
    await db_session.refresh(sub)
    rows = (await db_session.scalars(select(EmailNotification))).all()
    assert payment.status == "succeeded" and sub.status == "active"
    assert len(rows) == 1 and rows[0].payment_id == payment.id
    assert rows[0].period_end == sub.current_period_end


async def test_failed_payment_transaction_rolls_back_queued_confirmation(db_session):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services.subscription_lifecycle import apply_subscription_provider_state

    _, sub, payment = await setup_paid(db_session)
    sub.status = "pending_payment"
    payment.status = "pending"
    payment.paid_at = None
    await db_session.commit()
    provider = {
        "status": "succeeded",
        "amount": {"currency": "RUB", "value": "1.00"},
        "metadata": {"payment_id": str(payment.id)},
    }
    await apply_subscription_provider_state(db_session, payment, provider, now=NOW)
    assert await db_session.scalar(select(func.count()).select_from(EmailNotification)) == 1
    await db_session.rollback()
    await db_session.refresh(payment)
    assert payment.status == "pending"
    assert await db_session.scalar(select(func.count()).select_from(EmailNotification)) == 0


@pytest.mark.parametrize("change", ["refunded", "blocked", "owner", "expired", "free"])
async def test_reminder_revalidates_owner_payment_user_and_plan(db_session, monkeypatch, change):
    from yleum_api.models.billing import FREE_PLAN_ID
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services import subscription_email as service

    user, sub, payment = await setup_paid(db_session)
    await db_session.commit()
    await service.enqueue_reminders(db_session, now=NOW)
    if change == "refunded":
        payment.status, payment.refunded_at = "refunded", NOW
    elif change == "blocked":
        user.status = "blocked"
    elif change == "owner":
        account = await db_session.get(BillingAccount, sub.billing_account_id)
        # Do not break the account's ownership constraint: replace its owner.
        other = User(email=f"{uuid4()}@example.test", status="active")
        db_session.add(other)
        await db_session.flush()
        account.personal_user_id = other.id
    elif change == "expired":
        sub.status = "expired"
    elif change == "free":
        sub.plan_id = FREE_PLAN_ID
    await db_session.commit()

    async def no_mail(**kwargs):
        pytest.fail("stale reminder must not be sent")

    monkeypatch.setattr(service, "send_transactional_email", no_mail)
    await service.deliver_one(async_sessionmaker(db_session.bind, expire_on_commit=False), now=NOW)
    row = await db_session.scalar(select(EmailNotification))
    await db_session.refresh(row)
    assert row.status == "skipped" and row.attempts == 0


async def test_stale_sending_claim_becomes_uncertain_without_replay(db_session, monkeypatch):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services import subscription_email as service

    _, sub, payment = await setup_paid(db_session)
    await service.enqueue_paid_email(
        db_session, payment, sub, await db_session.get(BillingPlan, sub.plan_id)
    )
    row = await db_session.scalar(select(EmailNotification))
    row.status, row.claimed_at, row.attempts = "sending", NOW - timedelta(hours=1), 1
    await db_session.commit()

    async def no_mail(**kwargs):
        pytest.fail("SMTP acceptance before worker crash is unknown")

    monkeypatch.setattr(service, "send_transactional_email", no_mail)
    assert not await service.deliver_one(
        async_sessionmaker(db_session.bind, expire_on_commit=False),
        now=NOW,
    )
    await db_session.refresh(row)
    assert row.status == "uncertain" and row.failure_category == "stale_claim"


async def test_refunded_paid_period_is_not_queued(db_session):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services.subscription_email import enqueue_reminders

    _, _, payment = await setup_paid(db_session)
    payment.status, payment.refunded_at = "refunded", NOW
    await db_session.commit()
    assert await enqueue_reminders(db_session, now=NOW) == 0
    assert await db_session.scalar(select(func.count()).select_from(EmailNotification)) == 0


async def test_other_worker_skips_locked_claim_and_sender_is_not_called(db_session, monkeypatch):
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services import subscription_email as service

    _, sub, payment = await setup_paid(db_session)
    await service.enqueue_paid_email(
        db_session, payment, sub, await db_session.get(BillingPlan, sub.plan_id)
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def no_mail(**kwargs):
        pytest.fail("another worker owns the locked claim")

    monkeypatch.setattr(service, "send_transactional_email", no_mail)
    async with factory() as owner, owner.begin():
        await owner.scalar(select(EmailNotification).with_for_update())
        assert not await service.deliver_one(factory, now=NOW)


@pytest.mark.parametrize(
    "authority,expected",
    [
        ("legacy", "skipped"),
        ("receipt", "sent"),
        ("phone", "sent"),
    ],
)
async def test_delivery_requires_canonical_email_authority_or_complete_phone_proof(
    db_session,
    monkeypatch,
    authority,
    expected,
):
    from yleum_api.models.account import AuthToken
    from yleum_api.models.email_notification import EmailNotification
    from yleum_api.services import subscription_email as service

    user, sub, payment = await setup_paid(db_session)
    user.email_verified_at = NOW
    if authority != "phone":
        user.phone_e164 = None
        user.phone_verified_at = None
    if authority == "receipt":
        db_session.add(
            AuthToken(
                user_id=user.id,
                purpose="verify_email",
                token_hash=uuid4().hex,
                used_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
    await service.enqueue_paid_email(
        db_session, payment, sub, await db_session.get(BillingPlan, sub.plan_id)
    )
    await db_session.commit()
    delivered = []

    async def send(**kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr(service, "send_transactional_email", send)
    await service.deliver_one(async_sessionmaker(db_session.bind, expire_on_commit=False), now=NOW)
    row = await db_session.scalar(select(EmailNotification))
    await db_session.refresh(row)
    assert row.status == expected
    assert len(delivered) == (1 if expected == "sent" else 0)


@pytest.mark.parametrize("partial", ["number_only", "timestamp_only"])
async def test_database_rejects_incomplete_phone_proof_before_email_delivery(db_session, partial):
    user, _, _ = await setup_paid(db_session)
    await db_session.commit()
    with pytest.raises(IntegrityError, match="phone_verified_identity"):
        async with db_session.begin_nested():
            if partial == "number_only":
                user.phone_verified_at = None
            else:
                user.phone_e164 = None
            await db_session.flush()
    await db_session.refresh(user)
    assert user.phone_e164 and user.phone_verified_at
