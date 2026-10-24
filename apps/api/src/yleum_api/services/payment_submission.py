"""Durable parameters for one provider operation, including its original receipt.

The caller commits preparation before POST and locks the payment while sending.
Losing a response never proves failure. Replay ends before the provider's 24h
idempotence guarantee; an unresolved older operation needs manual verification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from yleum_api.core.errors import ApiError
from yleum_api.models.account import Payment
from yleum_api.services import yookassa

_REPLAY_WINDOW = timedelta(hours=24, minutes=-1)


class PaymentSubmissionNeedsReview(yookassa.YooKassaUnavailable):
    """No safe POST is possible, but a verified webhook/GET may still settle it."""


def prepare_submission(
    payment: Payment,
    *,
    customer_email: str,
    title: str,
    return_url: str | None = None,
    payment_method_id: str | None = None,
    save_payment_method: bool = False,
    now: datetime | None = None,
) -> None:
    if payment.provider_payment_id or has_submission(payment):
        return
    amount = f"{payment.amount_rub:.2f}"
    arguments: dict[str, Any] = {
        "amount": amount,
        "description": title,
        "customer_email": customer_email,
        "idempotency_key": payment.idempotency_key,
        "metadata": {
            "payment_id": str(payment.id),
            "billing_account_id": str(payment.billing_account_id),
            "user_id": str(payment.user_id),
            "purpose": payment.purpose,
        },
        "receipt": yookassa.payment_receipt(customer_email, title, amount),
    }
    if payment.edit_units > 0:
        arguments["metadata"].update(
            edit_units=str(payment.edit_units),
            edits_only="true" if payment.edits_only else "false",
            package_version=str(payment.package_version),
        )
    if payment_method_id is not None:
        arguments["payment_method_id"] = payment_method_id
        kind = "recurring"
    elif return_url is not None:
        arguments.update(return_url=return_url, save_payment_method=save_payment_method)
        kind = "redirect"
    else:
        raise ValueError("payment needs a return URL or a saved method")
    payment.provider_payload = {
        **(payment.provider_payload or {}),
        "_submission": {
            "kind": kind,
            "started_at": (now or datetime.now(UTC)).isoformat(),
            "arguments": arguments,
        },
    }


def has_submission(payment: Payment) -> bool:
    return isinstance((payment.provider_payload or {}).get("_submission"), dict)


def require_manual_review(payment: Payment) -> None:
    payment.provider_payload = {**(payment.provider_payload or {}), "_recovery_review": True}


def verify_provider_payment(payment: Payment, provider: dict[str, Any]) -> None:
    amount = provider.get("amount")
    try:
        value = Decimal(str(amount.get("value"))) if isinstance(amount, dict) else None
    except InvalidOperation as exc:
        raise ApiError("invalid_webhook", "invalid payment amount", 400) from exc
    if (
        not isinstance(amount, dict)
        or amount.get("currency") != "RUB"
        or value is None
        or not value.is_finite()
        or value != payment.amount_rub
    ):
        raise ApiError("invalid_webhook", "payment amount mismatch", 400)
    metadata = provider.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("payment_id") != str(payment.id):
        raise ApiError("invalid_webhook", "payment metadata mismatch", 400)
    for key, expected in (
        ("billing_account_id", str(payment.billing_account_id)),
        ("user_id", str(payment.user_id)),
        ("purpose", payment.purpose),
    ):
        if key in metadata and metadata[key] != expected:
            raise ApiError("invalid_webhook", "payment owner mismatch", 400)
    if payment.provider_payment_id and provider.get("id") != payment.provider_payment_id:
        raise ApiError("invalid_webhook", "provider identity mismatch", 400)
    if provider.get("status") not in {
        "pending",
        "waiting_for_capture",
        "succeeded",
        "canceled",
        "cancelled",
    }:
        raise ApiError("invalid_webhook", "invalid provider state", 400)


async def send_saved_payment(
    payment: Payment,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    saved = (payment.provider_payload or {}).get("_submission")
    if not isinstance(saved, dict) or not isinstance(saved.get("arguments"), dict):
        raise PaymentSubmissionNeedsReview("original payment parameters are unavailable")
    try:
        started_at = datetime.fromisoformat(str(saved["started_at"]))
        if started_at.tzinfo is None:
            raise ValueError("missing timezone")
    except (KeyError, ValueError) as exc:
        raise PaymentSubmissionNeedsReview("payment replay time is unavailable") from exc
    current = now or datetime.now(UTC)
    if current < started_at or current - started_at >= _REPLAY_WINDOW:
        raise PaymentSubmissionNeedsReview("payment replay window expired; verify with provider")
    arguments = saved["arguments"]
    if arguments.get("idempotency_key") != payment.idempotency_key:
        raise PaymentSubmissionNeedsReview("saved payment identity mismatch")
    if saved.get("kind") == "recurring":
        provider = await yookassa.create_recurring_payment(**arguments)
    elif saved.get("kind") == "redirect":
        provider = await yookassa.create_payment(**arguments)
    else:
        raise PaymentSubmissionNeedsReview("unknown payment request kind")
    if not provider.get("id") or provider.get("status") not in {
        "pending",
        "waiting_for_capture",
        "succeeded",
        "canceled",
        "cancelled",
    }:
        raise yookassa.YooKassaUnavailable("YooKassa returned an incomplete payment")
    return provider


def bind_provider_payment(payment: Payment, provider: dict[str, Any]) -> None:
    provider_id = str(provider.get("id") or "")
    if not provider_id or (
        payment.provider_payment_id is not None and payment.provider_payment_id != provider_id
    ):
        raise yookassa.YooKassaUnavailable("provider payment identity mismatch")
    payment.provider_payment_id = provider_id
    confirmation = provider.get("confirmation")
    if isinstance(confirmation, dict) and confirmation.get("confirmation_url"):
        payment.confirmation_url = str(confirmation["confirmation_url"])
