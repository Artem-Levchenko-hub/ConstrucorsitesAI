from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from omnia_api.core.admin import is_admin_user
from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.account import Payment, WalletLedgerEntry
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet
from omnia_api.schemas.payments import PaymentConfigPublic, PaymentCreate, PaymentPublic
from omnia_api.services import yookassa

router = APIRouter(prefix="/api/payments", tags=["payments"])

PACKAGES: dict[str, tuple[Decimal, Decimal, str]] = {
    "start": (Decimal("490.00"), Decimal("500.00"), "Стартовый пакет MAX Studio"),
    "business": (Decimal("1490.00"), Decimal("1600.00"), "Бизнес-пакет MAX Studio"),
    "pro": (Decimal("3990.00"), Decimal("4500.00"), "Профессиональный пакет MAX Studio"),
}


def _provider_status(value: object) -> str:
    status_value = str(value or "pending")
    if status_value in {"canceled", "cancelled"}:
        return "cancelled"
    if status_value in {"pending", "waiting_for_capture", "succeeded"}:
        return status_value
    return "failed"


def _configured() -> bool:
    settings = get_settings()
    return bool(
        yookassa.configured() and settings.legal_operator_name and settings.legal_operator_inn
    )


def _is_admin(user: User) -> bool:
    return is_admin_user(user)


@router.get("/config", response_model=PaymentConfigPublic)
async def get_payment_config() -> PaymentConfigPublic:
    enabled = _configured()
    return PaymentConfigPublic(
        enabled=enabled,
        packages=[
            {
                "code": code,
                "price_rub": str(price),
                "credit_rub": str(credit),
                "title": title,
            }
            for code, (price, credit, title) in PACKAGES.items()
        ],
        reason=None if enabled else "Платежи включатся после подключения магазина ЮKassa",
    )


@router.get("", response_model=list[PaymentPublic])
async def list_payments(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> list[Payment]:
    return list(
        (
            await session.execute(
                select(Payment)
                .where(Payment.user_id == current_user.id)
                .order_by(Payment.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )


@router.post("", response_model=PaymentPublic, status_code=status.HTTP_201_CREATED)
async def create_payment(
    payload: PaymentCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> Payment:
    if not _configured():
        raise ApiError(
            "payments_unavailable",
            "Платежи ещё не подключены",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if current_user.email is None or current_user.email_verified_at is None:
        raise ApiError(
            "email_verification_required",
            "Подтвердите email перед оплатой",
            status.HTTP_403_FORBIDDEN,
        )
    key = str(payload.idempotency_key)
    existing = (
        await session.execute(select(Payment).where(Payment.idempotency_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.user_id != current_user.id:
            raise ApiError("conflict", "idempotency key conflict", status.HTTP_409_CONFLICT)
        return existing

    price, credit, title = PACKAGES[payload.package_code]
    payment = Payment(
        user_id=current_user.id,
        idempotency_key=key,
        package_code=payload.package_code,
        amount_rub=price,
        credit_rub=credit,
        status="pending",
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    settings = get_settings()
    try:
        provider = await yookassa.create_payment(
            amount=f"{price:.2f}",
            description=title,
            return_url=f"{settings.web_base_url.rstrip('/')}/account?payment={payment.id}",
            customer_email=str(current_user.email),
            idempotency_key=key,
            metadata={"payment_id": str(payment.id), "user_id": str(current_user.id)},
        )
    except yookassa.YooKassaUnavailable as exc:
        payment.status = "failed"
        await session.commit()
        raise ApiError(
            "payment_provider_unavailable",
            "ЮKassa временно недоступна",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    provider_payment_id = str(provider.get("id") or "")
    if not provider_payment_id:
        payment.status = "failed"
        await session.commit()
        raise ApiError(
            "payment_provider_unavailable",
            "ЮKassa вернула неполный ответ",
            status.HTTP_502_BAD_GATEWAY,
        )
    payment.provider_payment_id = provider_payment_id
    payment.status = _provider_status(provider.get("status"))
    confirmation = provider.get("confirmation")
    if isinstance(confirmation, dict):
        payment.confirmation_url = str(confirmation.get("confirmation_url") or "") or None
    payment.provider_payload = provider
    await session.commit()
    await session.refresh(payment)
    return payment


async def _apply_provider_state(
    session: SessionDep,
    payment: Payment,
    provider: dict[str, object],
) -> None:
    provider_status = _provider_status(provider.get("status"))
    amount = provider.get("amount")
    if not isinstance(amount, dict):
        raise ApiError("invalid_webhook", "invalid payment amount", status.HTTP_400_BAD_REQUEST)
    if amount.get("currency") != "RUB" or Decimal(str(amount.get("value"))) != payment.amount_rub:
        raise ApiError("invalid_webhook", "payment amount mismatch", status.HTTP_400_BAD_REQUEST)

    payment.provider_payload = provider
    if provider_status == "succeeded" and payment.status != "succeeded":
        wallet = (
            await session.execute(
                select(Wallet).where(Wallet.user_id == payment.user_id).with_for_update()
            )
        ).scalar_one()
        wallet.balance_rub += payment.credit_rub
        payment.status = "succeeded"
        payment.paid_at = datetime.now(UTC)
        session.add(
            WalletLedgerEntry(
                user_id=payment.user_id,
                entry_type="payment",
                amount_rub=payment.credit_rub,
                balance_after_rub=wallet.balance_rub,
                external_ref=f"payment:{payment.id}",
                description=f"Пополнение через ЮKassa ({payment.package_code})",
            )
        )
    elif provider_status == "cancelled" and payment.status not in {"succeeded", "refunded"}:
        payment.status = "cancelled"
        payment.cancelled_at = datetime.now(UTC)
    elif provider_status == "waiting_for_capture":
        payment.status = "waiting_for_capture"


@router.post("/yookassa/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def yookassa_webhook(request: Request, session: SessionDep) -> None:
    body = await request.json()
    obj = body.get("object") if isinstance(body, dict) else None
    provider_id = str(obj.get("id") or "") if isinstance(obj, dict) else ""
    if not provider_id:
        raise ApiError("invalid_webhook", "payment id missing", status.HTTP_400_BAD_REQUEST)
    payment = (
        await session.execute(
            select(Payment).where(Payment.provider_payment_id == provider_id).with_for_update()
        )
    ).scalar_one_or_none()
    if payment is None:
        # Unknown provider events are acknowledged so retries cannot amplify a
        # stale or foreign notification.
        return
    try:
        provider = await yookassa.get_payment(provider_id)
    except yookassa.YooKassaUnavailable as exc:
        raise ApiError(
            "payment_provider_unavailable",
            "ЮKassa confirmation failed",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    metadata = provider.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("payment_id") != str(payment.id):
        raise ApiError("invalid_webhook", "payment metadata mismatch", status.HTTP_400_BAD_REQUEST)
    await _apply_provider_state(session, payment, provider)
    await session.commit()


@router.post("/{payment_id}/reconcile", response_model=PaymentPublic)
async def reconcile_payment(
    payment_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> Payment:
    payment = (
        await session.execute(select(Payment).where(Payment.id == payment_id).with_for_update())
    ).scalar_one_or_none()
    if payment is None or (
        payment.user_id != current_user.id and not _is_admin(current_user)
    ):
        raise ApiError("not_found", "payment not found", status.HTTP_404_NOT_FOUND)
    if not payment.provider_payment_id:
        return payment
    try:
        provider = await yookassa.get_payment(payment.provider_payment_id)
    except yookassa.YooKassaUnavailable as exc:
        raise ApiError(
            "payment_provider_unavailable",
            "ЮKassa временно недоступна",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    await _apply_provider_state(session, payment, provider)
    await session.commit()
    await session.refresh(payment)
    return payment


@router.post("/{payment_id}/refund", response_model=PaymentPublic)
async def refund_payment(
    payment_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> Payment:
    if not _is_admin(current_user):
        raise ApiError("forbidden", "admin access required", status.HTTP_403_FORBIDDEN)
    payment = (
        await session.execute(select(Payment).where(Payment.id == payment_id).with_for_update())
    ).scalar_one_or_none()
    if payment is None:
        raise ApiError("not_found", "payment not found", status.HTTP_404_NOT_FOUND)
    if payment.status != "succeeded" or not payment.provider_payment_id:
        raise ApiError("refund_unavailable", "payment cannot be refunded", status.HTTP_409_CONFLICT)
    wallet = (
        await session.execute(
            select(Wallet).where(Wallet.user_id == payment.user_id).with_for_update()
        )
    ).scalar_one()
    if wallet.balance_rub < payment.credit_rub:
        raise ApiError(
            "refund_balance_used",
            "Зачисленные средства уже использованы",
            status.HTTP_409_CONFLICT,
        )
    try:
        await yookassa.create_refund(
            provider_payment_id=payment.provider_payment_id,
            amount=f"{payment.amount_rub:.2f}",
            idempotency_key=f"refund-{payment.id}",
        )
    except yookassa.YooKassaUnavailable as exc:
        raise ApiError(
            "payment_provider_unavailable",
            "ЮKassa временно недоступна",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    wallet.balance_rub -= payment.credit_rub
    payment.status = "refunded"
    payment.refunded_at = datetime.now(UTC)
    session.add(
        WalletLedgerEntry(
            user_id=payment.user_id,
            entry_type="refund",
            amount_rub=-payment.credit_rub,
            balance_after_rub=wallet.balance_rub,
            external_ref=f"refund:{payment.id}",
            description="Возврат платежа через ЮKassa",
        )
    )
    await session.commit()
    await session.refresh(payment)
    return payment
