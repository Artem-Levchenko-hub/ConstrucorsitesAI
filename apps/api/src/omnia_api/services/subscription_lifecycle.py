from __future__ import annotations

import asyncio
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from omnia_api.core.config import get_settings
from omnia_api.core.errors import ApiError
from omnia_api.models.account import BusinessMember, Payment
from omnia_api.models.billing import (
    FREE_PLAN_ID,
    BillingAccount,
    BillingPaymentMethod,
    BillingPlan,
    Subscription,
)
from omnia_api.models.project import Project
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet
from omnia_api.models.wallet_charge import WalletCharge
from omnia_api.services import orchestrator_client, yookassa
from omnia_api.services.transactional_email import (
    EmailDeliveryFailed,
    EmailDeliveryNotConfigured,
    send_transactional_email,
)

log = structlog.get_logger(__name__)
LIVE_SUBSCRIPTION_STATUSES = ("trialing", "active", "past_due", "paused")


def one_month_after(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def provider_status(value: object) -> str:
    result = str(value or "pending")
    if result in {"canceled", "cancelled"}:
        return "cancelled"
    if result in {"pending", "waiting_for_capture", "succeeded"}:
        return result
    return "failed"


async def _credit_subscription(
    session: AsyncSession,
    *,
    payment: Payment,
    subscription: Subscription,
    plan: BillingPlan,
) -> None:
    external_ref = f"subscription-credit:{payment.id}"
    already_applied = (
        await session.execute(
            select(WalletCharge.id).where(WalletCharge.external_ref == external_ref)
        )
    ).scalar_one_or_none()
    if already_applied is not None or payment.credit_rub <= 0:
        return
    wallet = (
        await session.execute(
            select(Wallet)
            .where(Wallet.billing_account_id == payment.billing_account_id)
            .with_for_update()
        )
    ).scalar_one()
    wallet.balance_rub += payment.credit_rub
    session.add(
        WalletCharge(
            billing_account_id=payment.billing_account_id,
            user_id=payment.user_id,
            subscription_id=subscription.id,
            entry_type="subscription_credit",
            amount_rub=payment.credit_rub,
            balance_after_rub=wallet.balance_rub,
            external_ref=external_ref,
            description=f"Кредит тарифа {plan.name} v{plan.version}",
        )
    )


async def _attach_saved_method(
    session: AsyncSession,
    *,
    payment: Payment,
    subscription: Subscription,
    provider: dict[str, Any],
    now: datetime,
) -> bool:
    method_payload = provider.get("payment_method")
    if (
        not subscription.auto_renew
        or not subscription.renewal_consent_version
        or not isinstance(method_payload, dict)
        or method_payload.get("saved") is not True
    ):
        return False
    provider_method_id = str(method_payload.get("id") or "")
    if not provider_method_id:
        return False
    method = (
        await session.execute(
            select(BillingPaymentMethod)
            .where(
                BillingPaymentMethod.provider == "yookassa",
                BillingPaymentMethod.provider_payment_method_id == provider_method_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    card = method_payload.get("card")
    last4 = (str(card.get("last4") or "") or None) if isinstance(card, dict) else None
    if method is None:
        method = BillingPaymentMethod(
            billing_account_id=payment.billing_account_id,
            user_id=payment.user_id,
            provider="yookassa",
            provider_payment_method_id=provider_method_id,
            status="active",
            title=str(method_payload.get("title") or "") or None,
            last4=last4,
            consent_version=subscription.renewal_consent_version,
            consented_at=subscription.renewal_consented_at or now,
        )
        session.add(method)
        await session.flush()
    elif method.billing_account_id != payment.billing_account_id:
        raise ApiError("invalid_webhook", "payment method owner mismatch", 400)
    else:
        method.status = "active"
        method.title = str(method_payload.get("title") or "") or method.title
        method.last4 = last4 or method.last4
        method.consent_version = subscription.renewal_consent_version
        method.consented_at = subscription.renewal_consented_at or now
        method.revoked_at = None
    subscription.payment_method_id = method.id
    return True


async def activate_initial_payment(
    session: AsyncSession,
    payment: Payment,
    provider: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    if payment.subscription_id is None:
        raise ApiError("invalid_webhook", "subscription payment is not linked", 400)
    subscription = (
        await session.execute(
            select(Subscription)
            .where(Subscription.id == payment.subscription_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        subscription is None
        or subscription.billing_account_id != payment.billing_account_id
        or subscription.status != "pending_payment"
    ):
        raise ApiError("invalid_webhook", "subscription is not awaiting payment", 409)
    plan = await session.get(BillingPlan, subscription.plan_id)
    if plan is None or plan.price_rub != payment.amount_rub:
        raise ApiError("invalid_webhook", "subscription plan mismatch", 400)

    live_subscriptions = list(
        (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.billing_account_id == payment.billing_account_id,
                    Subscription.id != subscription.id,
                    Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
                )
                .with_for_update()
            )
        ).scalars()
    )
    current_time = now or datetime.now(UTC)
    for live in live_subscriptions:
        live.status = "expired"
        live.auto_renew = False
        live.cancel_at_period_end = False
        live.next_charge_at = None
        live.ended_at = current_time
    await session.flush()

    method_saved = await _attach_saved_method(
        session,
        payment=payment,
        subscription=subscription,
        provider=provider,
        now=current_time,
    )
    if subscription.auto_renew and not method_saved:
        # A successful first purchase must never be rolled back because the
        # provider declined tokenization. Continue the paid month safely
        # without future charges.
        subscription.auto_renew = False
        subscription.renewal_consent_version = None
        subscription.renewal_consented_at = None

    subscription.status = "active"
    subscription.current_period_start = current_time
    subscription.current_period_end = one_month_after(current_time)
    subscription.next_charge_at = (
        subscription.current_period_end if subscription.auto_renew else None
    )
    subscription.grace_period_ends_at = None
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.ended_at = None
    configured_slots = plan.entitlements.get("always_on_slots")
    await enforce_keep_alive_entitlement(
        session,
        account_id=subscription.billing_account_id,
        allowed_slots=configured_slots if isinstance(configured_slots, int) else 0,
    )
    await _credit_subscription(
        session,
        payment=payment,
        subscription=subscription,
        plan=plan,
    )


async def activate_renewal_payment(
    session: AsyncSession,
    payment: Payment,
    *,
    now: datetime | None = None,
) -> None:
    if payment.subscription_id is None:
        raise ApiError("invalid_webhook", "renewal is not linked", 400)
    subscription = (
        await session.execute(
            select(Subscription)
            .where(Subscription.id == payment.subscription_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        subscription is None
        or subscription.billing_account_id != payment.billing_account_id
        or subscription.status not in LIVE_SUBSCRIPTION_STATUSES
    ):
        raise ApiError("invalid_webhook", "subscription cannot be renewed", 409)
    plan = await session.get(BillingPlan, subscription.plan_id)
    if plan is None or plan.price_rub != payment.amount_rub:
        raise ApiError("invalid_webhook", "renewal plan mismatch", 400)

    current_time = now or datetime.now(UTC)
    period_start = subscription.current_period_end or current_time
    subscription.status = "active"
    subscription.current_period_start = period_start
    subscription.current_period_end = one_month_after(period_start)
    subscription.next_charge_at = (
        subscription.current_period_end if subscription.auto_renew else None
    )
    subscription.grace_period_ends_at = None
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.ended_at = None
    await _credit_subscription(
        session,
        payment=payment,
        subscription=subscription,
        plan=plan,
    )


async def apply_subscription_provider_state(
    session: AsyncSession,
    payment: Payment,
    provider: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    status_value = provider_status(provider.get("status"))
    amount = provider.get("amount")
    if not isinstance(amount, dict):
        raise ApiError("invalid_webhook", "invalid payment amount", 400)
    if amount.get("currency") != "RUB" or Decimal(str(amount.get("value"))) != payment.amount_rub:
        raise ApiError("invalid_webhook", "payment amount mismatch", 400)
    metadata = provider.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("payment_id") != str(payment.id):
        raise ApiError("invalid_webhook", "payment metadata mismatch", 400)
    payment.provider_payload = provider
    current_time = now or datetime.now(UTC)
    if status_value == "succeeded" and payment.status != "succeeded":
        if payment.purpose == "subscription_initial":
            await activate_initial_payment(session, payment, provider, now=current_time)
        elif payment.purpose == "subscription_renewal":
            await activate_renewal_payment(session, payment, now=current_time)
        else:
            raise ApiError("invalid_webhook", "unsupported subscription payment", 400)
        payment.status = "succeeded"
        payment.paid_at = current_time
    elif status_value == "cancelled" and payment.status not in {"succeeded", "refunded"}:
        payment.status = "cancelled"
        payment.cancelled_at = current_time
    elif status_value == "waiting_for_capture":
        payment.status = "waiting_for_capture"
    elif status_value == "failed" and payment.status not in {"succeeded", "refunded"}:
        payment.status = "failed"
    return status_value


async def _account_user_ids(session: AsyncSession, account: BillingAccount) -> list[UUID]:
    if account.scope == "personal" and account.personal_user_id is not None:
        return [account.personal_user_id]
    if account.business_id is None:
        return []
    return list(
        (
            await session.execute(
                select(BusinessMember.user_id).where(
                    BusinessMember.business_id == account.business_id
                )
            )
        ).scalars()
    )


async def enforce_keep_alive_entitlement(
    session: AsyncSession,
    *,
    account_id: UUID,
    allowed_slots: int,
) -> None:
    account = await session.get(BillingAccount, account_id)
    if account is None:
        raise RuntimeError("subscription billing account is missing")
    user_ids = await _account_user_ids(session, account)
    projects = list(
        (
            await session.execute(
                select(Project)
                .where(
                    Project.owner_id.in_(user_ids),
                    Project.keep_alive_enabled.is_(True),
                )
                .order_by(Project.created_at, Project.id)
            )
        ).scalars()
    )
    for project in projects[max(0, allowed_slots) :]:
        project.keep_alive_enabled = False
        try:
            await orchestrator_client.set_keep_alive(project.id, enabled=False)
        except Exception:
            log.warning("subscription.keep_alive_revoke_failed", project_id=str(project.id))


async def _notify(user_id: UUID, *, subject: str, text: str, session: AsyncSession) -> None:
    email = await session.scalar(select(User.email).where(User.id == user_id))
    if not email:
        return
    try:
        await send_transactional_email(recipient=str(email), subject=subject, text=text)
    except (EmailDeliveryNotConfigured, EmailDeliveryFailed):
        log.warning("subscription.notification_failed", user_id=str(user_id), subject=subject)


async def downgrade_to_free(
    session: AsyncSession,
    subscription: Subscription,
    *,
    now: datetime,
) -> None:
    subscription.status = "expired"
    subscription.auto_renew = False
    subscription.cancel_at_period_end = False
    subscription.next_charge_at = None
    subscription.grace_period_ends_at = None
    subscription.ended_at = now
    await session.flush()
    session.add(
        Subscription(
            billing_account_id=subscription.billing_account_id,
            user_id=subscription.user_id,
            plan_id=FREE_PLAN_ID,
            status="active",
            auto_renew=False,
        )
    )
    await enforce_keep_alive_entitlement(
        session,
        account_id=subscription.billing_account_id,
        allowed_slots=0,
    )
    await _notify(
        subscription.user_id,
        subject="Подписка Omnia переведена на Free",
        text=(
            "Льготный период завершился. Платный тариф отключён, списаний больше не будет. "
            "Проекты и данные сохранены в режиме Free."
        ),
        session=session,
    )


async def _mark_past_due(
    session: AsyncSession,
    subscription: Subscription,
    *,
    now: datetime,
) -> None:
    first_failure = subscription.status != "past_due"
    subscription.status = "past_due"
    base = subscription.current_period_end or now
    if subscription.grace_period_ends_at is None:
        subscription.grace_period_ends_at = base + timedelta(
            days=get_settings().billing_grace_days
        )
    subscription.next_charge_at = now + timedelta(
        hours=get_settings().billing_renewal_retry_hours
    )
    if first_failure:
        await _notify(
            subscription.user_id,
            subject="Не удалось продлить подписку Omnia",
            text=(
                f"Мы повторим оплату до {subscription.grace_period_ends_at:%d.%m.%Y}. "
                "До конца льготного периода платные возможности остаются активны."
            ),
            session=session,
        )


async def _renew_subscription(
    session: AsyncSession,
    subscription: Subscription,
    *,
    now: datetime,
) -> None:
    pending = (
        await session.execute(
            select(Payment)
            .where(
                Payment.subscription_id == subscription.id,
                Payment.purpose == "subscription_renewal",
                Payment.status.in_(("pending", "waiting_for_capture")),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pending is not None and pending.provider_payment_id:
        try:
            provider = await yookassa.get_payment(pending.provider_payment_id)
        except yookassa.YooKassaUnavailable:
            subscription.next_charge_at = now + timedelta(
                hours=get_settings().billing_renewal_retry_hours
            )
            await session.commit()
            return
        state = await apply_subscription_provider_state(
            session, pending, provider, now=now
        )
        if state in {"cancelled", "failed"}:
            await _mark_past_due(session, subscription, now=now)
        elif state in {"pending", "waiting_for_capture"}:
            subscription.next_charge_at = now + timedelta(
                hours=get_settings().billing_renewal_retry_hours
            )
        await session.commit()
        return
    if pending is not None:
        pending.status = "failed"

    if (
        subscription.grace_period_ends_at is not None
        and subscription.grace_period_ends_at <= now
    ):
        await downgrade_to_free(session, subscription, now=now)
        await session.commit()
        return

    method = (
        await session.execute(
            select(BillingPaymentMethod).where(
                BillingPaymentMethod.id == subscription.payment_method_id,
                BillingPaymentMethod.status == "active",
            )
        )
    ).scalar_one_or_none()
    plan = await session.get(BillingPlan, subscription.plan_id)
    user = await session.get(User, subscription.user_id)
    if method is None or plan is None or user is None or not user.email:
        await _mark_past_due(session, subscription, now=now)
        await session.commit()
        return

    attempt = (
        await session.scalar(
            select(func.count())
            .select_from(Payment)
            .where(
                Payment.subscription_id == subscription.id,
                Payment.purpose == "subscription_renewal",
            )
        )
        or 0
    ) + 1
    period_key = (subscription.current_period_end or now).date().isoformat()
    payment = Payment(
        billing_account_id=subscription.billing_account_id,
        user_id=subscription.user_id,
        idempotency_key=f"renewal:{subscription.id}:{period_key}:{attempt}",
        purpose="subscription_renewal",
        subscription_id=subscription.id,
        package_code=f"subscription:{plan.code}:v{plan.version}",
        amount_rub=plan.price_rub,
        credit_rub=plan.included_credit_rub,
        status="pending",
    )
    session.add(payment)
    await session.commit()
    try:
        provider = await yookassa.create_recurring_payment(
            amount=f"{payment.amount_rub:.2f}",
            description=f"Продление Omnia {plan.name}, 1 месяц",
            customer_email=str(user.email),
            payment_method_id=method.provider_payment_method_id,
            idempotency_key=payment.idempotency_key,
            metadata={
                "payment_id": str(payment.id),
                "billing_account_id": str(payment.billing_account_id),
                "user_id": str(payment.user_id),
                "purpose": payment.purpose,
            },
        )
    except yookassa.YooKassaUnavailable:
        payment.status = "failed"
        await _mark_past_due(session, subscription, now=now)
        await session.commit()
        return
    payment.provider_payment_id = str(provider.get("id") or "") or None
    if payment.provider_payment_id is None:
        payment.status = "failed"
        await _mark_past_due(session, subscription, now=now)
        await session.commit()
        return
    state = await apply_subscription_provider_state(session, payment, provider, now=now)
    if state in {"cancelled", "failed"}:
        await _mark_past_due(session, subscription, now=now)
    elif state in {"pending", "waiting_for_capture"}:
        await _mark_past_due(session, subscription, now=now)
    await session.commit()


async def process_subscription_cycle(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    subscriptions = list(
        (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
                    Subscription.current_period_end.is_not(None),
                    Subscription.current_period_end <= current_time,
                )
                .order_by(Subscription.current_period_end)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    processed = 0
    for subscription in subscriptions:
        if subscription.cancel_at_period_end or not subscription.auto_renew:
            await downgrade_to_free(session, subscription, now=current_time)
            await session.commit()
        elif (
            subscription.grace_period_ends_at is not None
            and subscription.grace_period_ends_at <= current_time
        ):
            await downgrade_to_free(session, subscription, now=current_time)
            await session.commit()
        elif (
            subscription.next_charge_at is None
            or subscription.next_charge_at <= current_time
        ):
            await _renew_subscription(session, subscription, now=current_time)
        else:
            continue
        processed += 1
    return processed


async def run_subscription_lifecycle_forever() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            try:
                async with factory() as session:
                    processed = await process_subscription_cycle(session)
                if processed:
                    log.info("subscription.lifecycle_cycle", processed=processed)
            except Exception:
                log.exception("subscription.lifecycle_cycle_failed")
            await asyncio.sleep(settings.billing_lifecycle_poll_seconds)
    finally:
        await engine.dispose()
