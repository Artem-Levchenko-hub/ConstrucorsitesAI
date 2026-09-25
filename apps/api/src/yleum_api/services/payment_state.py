"""Apply a verified provider payment state to our ledger — one place for the
webhook, the manual reconcile endpoint and the background reconciliation.

A wallet top-up credits the wallet exactly once (`external_ref` guards the
ledger); a subscription payment is handed to `subscription_lifecycle`, which
activates or renews the plan in the same transaction. Nothing here commits:
the caller owns the transaction boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.account import Payment
from yleum_api.models.billing import Subscription
from yleum_api.models.wallet import Wallet
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.services import yookassa
from yleum_api.services.subscription_lifecycle import (
    apply_subscription_provider_state,
    provider_status,
)

log = structlog.get_logger(__name__)

OPEN_PAYMENT_STATUSES: tuple[str, ...] = ("pending", "waiting_for_capture")
# Only orders a person started in the cabinet are swept here; renewal charges
# stay with the subscription lifecycle, which owns their retry schedule.
RECONCILED_PURPOSES: tuple[str, ...] = ("wallet_topup", "subscription_initial")
_RECONCILE_BATCH = 50


async def cancel_pending_subscription(session: AsyncSession, payment: Payment) -> None:
    """A failed or cancelled first payment closes the plan that waited for it."""
    if payment.purpose != "subscription_initial" or payment.subscription_id is None:
        return
    subscription = await session.get(Subscription, payment.subscription_id)
    if subscription is None or subscription.status != "pending_payment":
        return
    now = datetime.now(UTC)
    subscription.status = "canceled"
    subscription.canceled_at = now
    subscription.ended_at = now


async def apply_provider_state(
    session: AsyncSession,
    payment: Payment,
    provider: dict[str, Any],
) -> str:
    """Move `payment` to the state the provider reports; returns that state."""
    if payment.purpose in {"subscription_initial", "subscription_renewal"}:
        state = await apply_subscription_provider_state(session, payment, provider)
        if state in {"cancelled", "failed"}:
            await cancel_pending_subscription(session, payment)
        return state
    state = provider_status(provider.get("status"))
    amount = provider.get("amount")
    if not isinstance(amount, dict):
        raise ApiError("invalid_webhook", "invalid payment amount", 400)
    if amount.get("currency") != "RUB" or Decimal(str(amount.get("value"))) != payment.amount_rub:
        raise ApiError("invalid_webhook", "payment amount mismatch", 400)

    payment.provider_payload = provider
    if state == "succeeded" and payment.status != "succeeded":
        if payment.purpose == "wallet_topup":
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
                    entry_type="payment",
                    amount_rub=payment.credit_rub,
                    balance_after_rub=wallet.balance_rub,
                    external_ref=f"payment:{payment.id}",
                    description=f"Пополнение через ЮKassa ({payment.package_code})",
                )
            )
        payment.status = "succeeded"
        payment.paid_at = datetime.now(UTC)
    elif state == "cancelled" and payment.status not in {"succeeded", "refunded"}:
        payment.status = "cancelled"
        payment.cancelled_at = datetime.now(UTC)
        await cancel_pending_subscription(session, payment)
    elif state == "waiting_for_capture":
        payment.status = "waiting_for_capture"
    elif state == "failed" and payment.status not in {"succeeded", "refunded"}:
        payment.status = "failed"
        await cancel_pending_subscription(session, payment)
    return state


def stale_payment_cutoffs(now: datetime) -> tuple[datetime, datetime]:
    """(re-read payments created before, abandon provider-less payments created before)."""
    settings = get_settings()
    return (
        now - timedelta(minutes=settings.billing_payment_reconcile_after_minutes),
        now - timedelta(hours=settings.billing_payment_abandon_after_hours),
    )


async def reconcile_pending_payments(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Close the gap a lost webhook leaves: re-read every order that stayed open
    too long and apply whatever the provider says. Returns how many changed.

    Rows are taken with `SKIP LOCKED`, so a second worker (or the webhook
    itself) never fights over the same payment. A provider outage stops the
    sweep for this cycle rather than half-applying a batch.
    """
    current = now or datetime.now(UTC)
    reread_before, abandon_before = stale_payment_cutoffs(current)
    payments = list(
        (
            await session.execute(
                select(Payment)
                .where(
                    Payment.status.in_(OPEN_PAYMENT_STATUSES),
                    Payment.purpose.in_(RECONCILED_PURPOSES),
                    Payment.created_at <= reread_before,
                )
                .order_by(Payment.created_at)
                .limit(_RECONCILE_BATCH)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    changed = 0
    for payment in payments:
        if not payment.provider_payment_id:
            # The provider never acknowledged this order (creation failed after
            # our row was written); once it is clearly abandoned, close it so the
            # cabinet stops showing a payment nobody can finish.
            if payment.created_at <= abandon_before:
                payment.status = "failed"
                await cancel_pending_subscription(session, payment)
                await session.commit()
                changed += 1
            continue
        try:
            provider = await yookassa.get_payment(payment.provider_payment_id)
        except yookassa.YooKassaUnavailable:
            log.warning("payments.reconcile_provider_unavailable", payment_id=str(payment.id))
            await session.rollback()
            break
        metadata = provider.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("payment_id") != str(payment.id):
            log.error("payments.reconcile_metadata_mismatch", payment_id=str(payment.id))
            await session.rollback()
            break
        before = payment.status
        try:
            await apply_provider_state(session, payment, provider)
        except ApiError as exc:
            log.error("payments.reconcile_rejected", payment_id=str(payment.id), reason=exc.message)
            await session.rollback()
            break
        await session.commit()
        if payment.status != before:
            changed += 1
            log.info(
                "payments.reconciled",
                payment_id=str(payment.id),
                purpose=payment.purpose,
                status=payment.status,
            )
    return changed


__all__ = [
    "OPEN_PAYMENT_STATUSES",
    "RECONCILED_PURPOSES",
    "apply_provider_state",
    "cancel_pending_subscription",
    "reconcile_pending_payments",
    "stale_payment_cutoffs",
]
