"""Issue signed read-only proofs of verified, single-item managed checkouts."""

import base64
import hmac
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.max_staff_checkout import MaxStaffPaymentAttempt
from yleum_api.services.max_staff_checkout import get_staff_checkout
from yleum_api.services.max_staff_source import read_staff_checkout_order

CHECKOUT_RECEIPT_PURPOSE = "yleum.max.checkout.receipt"
CHECKOUT_RECEIPT_TTL_SECONDS = 300
_KEY_DOMAIN = b"yleum:max-checkout-receipt:ed25519-seed:v1"
_SKU = re.compile(r"[a-zA-Z0-9_-]{1,64}")
_MAX_SAFE_INTEGER = (1 << 53) - 1


def _private_key() -> Ed25519PrivateKey:
    secret = get_settings().jwt_secret.get_secret_value().encode()
    if len(secret) < 32:
        raise ApiError("unavailable", "Подтверждение оплаты временно недоступно", 503)
    return Ed25519PrivateKey.from_private_bytes(hmac.digest(secret, _KEY_DOMAIN, "sha256"))


def checkout_receipt_public_key_pem() -> str:
    return _private_key().public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_checkout_receipt(
    claims: Mapping[str, object], *, private_key: Ed25519PrivateKey,
) -> str:
    """Encode exact claims from an already trusted issuer or isolated test runner."""
    payload = json.dumps(dict(claims), sort_keys=True, separators=(",", ":"), allow_nan=False)
    message = f"v1.{_b64url(payload.encode('utf-8'))}"
    return f"{message}.{_b64url(private_key.sign(message.encode('ascii')))}"


def _unconfirmed() -> ApiError:
    return ApiError("conflict", "Оплата или состав заказа не подтверждены", 409)


@dataclass(frozen=True)
class _PaidCheckout:
    id: UUID
    owner_id: UUID
    amount_kopecks: int
    test: bool
    provider_payment_id: str
    created_at: datetime
    paid_at: datetime
    verified_at: datetime


def _paid_checkout(
    row: MaxStaffPaymentAttempt, project_id: UUID, order_id: UUID, actor_id: int,
) -> _PaidCheckout:
    if (
        (row.project_id, row.order_id, row.actor_id) != (project_id, order_id, actor_id)
        or row.status != "succeeded" or type(row.is_test) is not bool
        or not row.provider_payment_id or row.paid_at is None or row.verified_at is None
        or not row.created_at <= row.paid_at <= row.verified_at
        or row.currency != "RUB" or type(row.amount_kopecks) is not int
        or not 0 < row.amount_kopecks <= _MAX_SAFE_INTEGER
    ):
        raise _unconfirmed()
    return _PaidCheckout(
        row.id, row.owner_id, row.amount_kopecks, row.is_test, row.provider_payment_id,
        row.created_at, row.paid_at, row.verified_at,
    )


async def issue_checkout_receipt(
    session: AsyncSession,
    project_id: UUID,
    order_id: UUID,
    actor_id: int,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    if type(actor_id) is not int or not 0 < actor_id < 2**63:
        raise _unconfirmed()
    paid = _paid_checkout(
        await get_staff_checkout(session, project_id, order_id, actor_id),
        project_id, order_id, actor_id,
    )
    order = await read_staff_checkout_order(session, project_id, order_id, actor_id)
    if (
        (order.id, order.owner_id, order.actor_id) != (order_id, paid.owner_id, actor_id)
        or order.created_at > paid.created_at or order.currency != "RUB"
        or order.total_kopecks != paid.amount_kopecks or len(order.items) != 1
    ):
        raise _unconfirmed()
    item = order.items[0]
    if (
        item.option is not None or not _SKU.fullmatch(item.catalog_id)
        or type(item.quantity) is not int or not 0 < item.quantity <= _MAX_SAFE_INTEGER
        or type(item.unit_price_kopecks) is not int or item.unit_price_kopecks <= 0
        or item.quantity * item.unit_price_kopecks != paid.amount_kopecks
    ):
        raise _unconfirmed()
    # The authoritative Core read crossed an I/O boundary. Recheck the local
    # payment and current owner before signing any claim.
    if paid != _paid_checkout(
        await get_staff_checkout(session, project_id, order_id, actor_id),
        project_id, order_id, actor_id,
    ):
        raise _unconfirmed()
    iat = int(clock().timestamp())
    return sign_checkout_receipt({
        "purpose": CHECKOUT_RECEIPT_PURPOSE,
        "project_id": str(project_id), "actor_id": str(actor_id), "order_id": str(order_id),
        "sku": item.catalog_id, "quantity": item.quantity, "amount_kopecks": paid.amount_kopecks,
        "currency": "RUB", "test": paid.test, "iat": iat, "exp": iat + CHECKOUT_RECEIPT_TTL_SECONDS,
    }, private_key=_private_key())
