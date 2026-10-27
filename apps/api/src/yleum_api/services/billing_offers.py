"""Operator-issued one-ruble payment/email test. No product fulfillment."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.admin import is_admin_user
from yleum_api.core.errors import ApiError
from yleum_api.models.account import Payment
from yleum_api.models.billing_offer import BillingOfferPayment, BillingPurchaseOffer
from yleum_api.models.user import User
from yleum_api.services.billing_accounts import resolve_billing_account

OFFER_AMOUNT = Decimal("1.00")
OPEN_PAYMENT_STATUSES = ("pending", "waiting_for_capture")


def _unavailable() -> ApiError:
    # Checkout calls this only after looking up the request's idempotency key.
    # Its browser intent can be released; another key's paid invoice is intact.
    return ApiError(
        "conflict",
        "Проверочная оплата по этому предложению недоступна",
        409,
        details={"payment_test_not_created": True},
    )


async def issue_billing_purchase_offer(
    session: AsyncSession,
    *,
    user_id: UUID,
    issued_by_user_id: UUID,
    now: datetime | None = None,
) -> BillingPurchaseOffer:
    """Internal operator entry point. Caller commits; no provider or SMTP call.

    One grant per account including after consumption/expiry. Repeated issuance
    returns an existing usable grant without extending its seven-day deadline.
    Current subscriptions, including legacy Pro, are deliberately not read.
    """
    issuer = await session.get(User, issued_by_user_id)
    target = await session.get(User, user_id)
    if issuer is None or issuer.status != "active" or not is_admin_user(issuer):
        raise ApiError("forbidden", "Only an active operator can issue this offer", 403)
    if target is None or target.status != "active" or target.is_anon:
        raise ApiError("not_found", "Active account not found", 404)
    account = await resolve_billing_account(session, user_id, for_update=True)
    current_time = now or datetime.now(UTC)
    existing = await session.scalar(
        select(BillingPurchaseOffer).where(BillingPurchaseOffer.billing_account_id == account.id)
    )
    if existing is not None:
        if existing.consumed_at is not None or existing.expires_at <= current_time:
            raise _unavailable()
        return existing
    offer = BillingPurchaseOffer(
        billing_account_id=account.id,
        user_id=user_id,
        issued_by_user_id=issued_by_user_id,
        amount_rub=OFFER_AMOUNT,
        currency="RUB",
        reason="manual_payment_email_test",
        created_at=current_time,
        expires_at=current_time + timedelta(days=7),
    )
    session.add(offer)
    await session.flush()
    return offer


async def current_purchase_offer(
    session: AsyncSession, *, user_id: UUID, account_id: UUID
) -> BillingPurchaseOffer | None:
    offer = await session.scalar(
        select(BillingPurchaseOffer).where(
            BillingPurchaseOffer.user_id == user_id,
            BillingPurchaseOffer.billing_account_id == account_id,
            BillingPurchaseOffer.consumed_at.is_(None),
        )
    )
    if offer is None:
        return None
    if offer.expires_at <= datetime.now(UTC):
        reserved = (
            await session.get(Payment, offer.reserved_payment_id)
            if (offer.reserved_payment_id is not None)
            else None
        )
        if reserved is None or reserved.status not in OPEN_PAYMENT_STATUSES:
            return None
    return offer


async def prepare_offer_checkout(
    session: AsyncSession,
    *,
    offer_id: UUID,
    user_id: UUID,
    account_id: UUID,
    idempotency_key: str,
) -> tuple[BillingPurchaseOffer, Payment | None]:
    """Serialize this grant only; release it before locking an old payment.

    Settlement locks payment before offer. We never hold an account or old
    payment lock while waiting here, so normal billing runs independently.
    """
    offer = await session.scalar(
        select(BillingPurchaseOffer)
        .where(
            BillingPurchaseOffer.id == offer_id,
            BillingPurchaseOffer.user_id == user_id,
            BillingPurchaseOffer.billing_account_id == account_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if offer is None:
        raise ApiError("not_found", "Personal offer not found", 404)
    # The grant lock serializes creation. Check the key under that lock before
    # availability: a concurrent attempt may already have settled or failed.
    existing = await session.scalar(
        select(Payment).where(Payment.idempotency_key == idempotency_key)
    )
    if existing is not None:
        link = await session.get(BillingOfferPayment, existing.id)
        if (
            existing.billing_account_id != account_id
            or existing.user_id != user_id
            or existing.purpose != "payment_test"
            or link is None
            or link.offer_id != offer.id
        ):
            raise ApiError("conflict", "idempotency key conflict", 409)
        return offer, existing
    if offer.currency != "RUB" or offer.amount_rub != OFFER_AMOUNT or offer.consumed_at is not None:
        raise _unavailable()
    reserved = (
        await session.get(Payment, offer.reserved_payment_id)
        if (offer.reserved_payment_id is not None)
        else None
    )
    if reserved is not None:
        if reserved.status in OPEN_PAYMENT_STATUSES:
            return offer, reserved
        if reserved.status not in ("cancelled", "failed"):
            raise _unavailable()
    if offer.expires_at <= datetime.now(UTC):
        raise _unavailable()
    return offer, None


async def reserve_offer_payment(
    session: AsyncSession, offer: BillingPurchaseOffer, payment: Payment
) -> None:
    await session.flush()
    offer.reserved_payment_id = payment.id
    session.add(BillingOfferPayment(payment_id=payment.id, offer_id=offer.id))
    await session.flush()


async def consume_payment_offer(
    session: AsyncSession,
    *,
    payment: Payment,
    now: datetime,
) -> None:
    """Only an exact audited reservation can settle a payment_test invoice."""
    link = await session.get(BillingOfferPayment, payment.id)
    offer = (
        await session.scalar(
            select(BillingPurchaseOffer)
            .where(BillingPurchaseOffer.id == link.offer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if link
        else None
    )
    if (
        offer is None
        or offer.consumed_at is not None
        or offer.reserved_payment_id != payment.id
        or offer.user_id != payment.user_id
        or offer.billing_account_id != payment.billing_account_id
        or payment.purpose != "payment_test"
        or payment.subscription_id is not None
        or payment.credit_rub != 0
        or payment.edit_units != 0
        or offer.currency != "RUB"
        or offer.amount_rub != OFFER_AMOUNT
        or payment.amount_rub != offer.amount_rub
        or not (offer.created_at <= payment.created_at <= offer.expires_at)
    ):
        raise ApiError("invalid_webhook", "Payment test grant mismatch", 400)
    offer.consumed_payment_id = payment.id
    offer.consumed_at = now
