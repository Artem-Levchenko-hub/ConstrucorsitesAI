"""Queue mail atomically with billing changes, then deliver outside that transaction."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.models.account import Payment
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.billing_offer import BillingOfferPayment, BillingPurchaseOffer
from yleum_api.models.email_notification import EmailNotification
from yleum_api.models.user import User
from yleum_api.services.accounts import has_verified_email
from yleum_api.services.email_templates import (
    EmailContent,
    paid_email,
    payment_test_email,
    reminder_email,
)
from yleum_api.services.transactional_email import (
    EmailDeliveryFailed,
    EmailDeliveryNotConfigured,
    send_transactional_email,
)

log = structlog.get_logger(__name__)
_CLAIM_TIMEOUT = timedelta(minutes=10)
_RETRY_AFTER = timedelta(minutes=5)


def reminder_days(period_end: datetime, now: datetime) -> int | None:
    remaining = period_end - now
    if timedelta(hours=48) < remaining <= timedelta(hours=72):
        return 3
    if timedelta(0) < remaining <= timedelta(hours=24):
        return 1
    return None


async def _enqueue(
    session: AsyncSession,
    payment: Payment,
    subscription: Subscription,
    plan: BillingPlan,
    *,
    event_type: str,
    event_key: str,
    now: datetime,
) -> bool:
    if subscription.current_period_end is None:
        return False
    result = await session.execute(
        insert(EmailNotification)
        .values(
            event_key=event_key,
            event_type=event_type,
            user_id=subscription.user_id,
            billing_account_id=subscription.billing_account_id,
            subscription_id=subscription.id,
            payment_id=payment.id,
            period_end=subscription.current_period_end,
            plan_name=plan.name,
            amount_rub=payment.amount_rub,
            available_at=now,
        )
        .on_conflict_do_nothing(index_elements=[EmailNotification.event_key])
        .returning(EmailNotification.id)
    )
    return result.scalar_one_or_none() is not None


async def enqueue_paid_email(
    session: AsyncSession,
    payment: Payment,
    subscription: Subscription,
    plan: BillingPlan,
) -> None:
    if payment.status != "succeeded" or payment.refunded_at is not None:
        return
    await _enqueue(
        session,
        payment,
        subscription,
        plan,
        event_type="paid",
        event_key=f"paid:{payment.id}",
        now=payment.paid_at or datetime.now(UTC),
    )


async def enqueue_reminders(session: AsyncSession, *, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    subscriptions = (
        await session.scalars(
            select(Subscription)
            .join(BillingPlan)
            .where(
                Subscription.status == "active",
                BillingPlan.price_rub > 0,
                Subscription.current_period_end > current,
                Subscription.current_period_end <= current + timedelta(hours=72),
            )
        )
    ).all()
    queued = 0
    for subscription in subscriptions:
        period_end = subscription.current_period_end
        if period_end is None or subscription.current_period_start is None:
            continue
        days = reminder_days(period_end, current)
        if days is None:
            continue
        # The most recent payment must cover this period. A refunded latest
        # payment cannot fall back to an earlier successful month's payment.
        payment = await session.scalar(
            select(Payment)
            .where(
                Payment.subscription_id == subscription.id,
                Payment.purpose.in_(("subscription_initial", "subscription_renewal")),
                Payment.paid_at >= subscription.current_period_start,
            )
            .order_by(Payment.paid_at.desc(), Payment.id.desc())
            .limit(1)
        )
        plan = await session.get(BillingPlan, subscription.plan_id)
        if payment is None or plan is None or payment.status != "succeeded" or payment.refunded_at:
            continue
        period = period_end.astimezone(UTC).isoformat()
        inserted = await _enqueue(
            session,
            payment,
            subscription,
            plan,
            event_type=f"ending_{days}",
            event_key=f"ending:{subscription.id}:{period}:{days}",
            now=current,
        )
        queued += int(inserted)
    return queued


async def enqueue_payment_test_email(session: AsyncSession, payment: Payment) -> None:
    """The test receipt shares the settlement transaction, without a subscription."""
    if (payment.purpose != "payment_test" or payment.status != "succeeded"
            or payment.subscription_id is not None or payment.refunded_at is not None):
        return
    await session.execute(
        insert(EmailNotification).values(
            event_key=f"payment_test:{payment.id}", event_type="payment_test",
            user_id=payment.user_id, billing_account_id=payment.billing_account_id,
            subscription_id=None, payment_id=payment.id, period_end=None,
            plan_name="Проверочная оплата", amount_rub=payment.amount_rub,
            available_at=payment.paid_at or datetime.now(UTC),
        ).on_conflict_do_nothing(index_elements=[EmailNotification.event_key])
    )


async def _validated_content(
    session: AsyncSession,
    event: EmailNotification,
    now: datetime,
) -> tuple[str, EmailContent] | None:
    account = await session.get(BillingAccount, event.billing_account_id)
    payment = await session.get(Payment, event.payment_id)
    user = await session.get(User, event.user_id)
    if (
        account is None
        or payment is None
        or user is None
        or user.status != "active"
        or user.is_anon
        or not user.email
        or account.scope != "personal"
        or account.personal_user_id != user.id
        or payment.user_id != user.id
        or payment.billing_account_id != account.id
        or payment.status != "succeeded"
        or payment.refunded_at is not None
    ):
        return None
    verified_phone = bool(user.phone_e164 and user.phone_verified_at)
    if not verified_phone and not await has_verified_email(session, user):
        return None
    if event.event_type == "payment_test":
        link = await session.get(BillingOfferPayment, payment.id)
        offer = await session.get(BillingPurchaseOffer, link.offer_id) if link else None
        if (payment.purpose != "payment_test" or payment.subscription_id is not None
                or offer is None or offer.consumed_payment_id != payment.id
                or offer.user_id != user.id or offer.billing_account_id != account.id
                or event.amount_rub != payment.amount_rub):
            return None
        return str(user.email), payment_test_email(
            amount_rub=event.amount_rub,
            manage_url=get_settings().web_base_url.rstrip("/") + "/billing/transactions",
        )
    subscription = await session.get(Subscription, event.subscription_id) if (
        event.subscription_id is not None
    ) else None
    if (subscription is None or event.period_end is None
            or subscription.user_id != user.id or subscription.billing_account_id != account.id
            or payment.subscription_id != subscription.id):
        return None
    manage_url = get_settings().web_base_url.rstrip("/") + "/billing/plan"
    if event.event_type == "paid":
        return str(user.email), paid_email(
            plan_name=event.plan_name,
            amount_rub=event.amount_rub,
            period_end=event.period_end,
            manage_url=manage_url,
        )
    days = int(event.event_type.rsplit("_", 1)[1])
    plan = await session.get(BillingPlan, subscription.plan_id)
    if (
        subscription.status != "active"
        or plan is None
        or plan.price_rub <= 0
        or subscription.current_period_end != event.period_end
        or reminder_days(event.period_end, now) != days
    ):
        return None
    return str(user.email), reminder_email(
        plan_name=event.plan_name,
        amount_rub=event.amount_rub,
        period_end=event.period_end,
        manage_url=manage_url,
        days=days,
        auto_renew=subscription.auto_renew and not subscription.cancel_at_period_end,
    )


async def deliver_one(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(UTC)
    async with factory() as session:
        async with session.begin():
            # A process may have died after SMTP accepted DATA. Never replay.
            await session.execute(
                update(EmailNotification)
                .where(
                    EmailNotification.status == "sending",
                    EmailNotification.claimed_at <= current - _CLAIM_TIMEOUT,
                )
                .values(status="uncertain", failure_category="stale_claim")
            )
            event = await session.scalar(
                select(EmailNotification)
                .where(
                    EmailNotification.status == "pending",
                    EmailNotification.available_at <= current,
                )
                .order_by(EmailNotification.available_at, EmailNotification.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if event is None:
                return False
            content = await _validated_content(session, event, current)
            if content is None:
                event.status = "skipped"
                return True
            event.status = "sending"
            event.claimed_at = current
            event.attempts += 1
            event_id = event.id
        # sending is visible to other workers before the first SMTP operation.
        recipient, message = content
        status, category = "sent", None
        try:
            await send_transactional_email(
                recipient=recipient,
                subject=message.subject,
                text=message.text,
                html=message.html,
                message_id=event_id,
            )
        except EmailDeliveryNotConfigured:
            status, category = "pending", "not_configured"
        except EmailDeliveryFailed as exc:
            status = "pending" if exc.retry_safe else "uncertain"
            category = exc.category
        except Exception:
            # Unexpected failures can happen after delivery; err on no duplicate.
            status, category = "uncertain", "unexpected"
        async with session.begin():
            await session.execute(
                update(EmailNotification)
                .where(
                    EmailNotification.id == event_id,
                    EmailNotification.status == "sending",
                )
                .values(
                    status=status,
                    failure_category=category,
                    available_at=current + _RETRY_AFTER,
                    sent_at=datetime.now(UTC) if status == "sent" else None,
                )
            )
        return True


async def run_subscription_email_loop(
    factory: async_sessionmaker[AsyncSession],
    *,
    poll_seconds: int,
) -> None:
    while True:
        try:
            async with factory() as session, session.begin():
                await enqueue_reminders(session)
            # Bound each pass so a full outbox cannot starve reminder discovery.
            for _ in range(100):
                if not await deliver_one(factory):
                    break
        except Exception:
            # Billing health is independent from this optional delivery channel.
            log.warning("subscription.email_cycle_failed")
        await asyncio.sleep(poll_seconds)
