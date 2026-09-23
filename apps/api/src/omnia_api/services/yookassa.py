from __future__ import annotations

import ipaddress
from typing import Any

import httpx

from omnia_api.core.config import get_settings

# Networks YooKassa sends HTTP notifications from (developer docs, "HTTP-уведомления",
# checked 2026-09-23). Production sets YOOKASSA_WEBHOOK_ALLOWED_CIDRS to this list;
# the constant exists so operators copy it instead of retyping it.
YOOKASSA_NOTIFICATION_NETWORKS: tuple[str, ...] = (
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
    "77.75.154.128/25",
    "2a02:5180::/32",
)


class YooKassaUnavailable(RuntimeError):
    pass


def configured() -> bool:
    settings = get_settings()
    return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)


def _credentials() -> tuple[str, str]:
    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise YooKassaUnavailable("YooKassa credentials are not configured")
    return settings.yookassa_shop_id, settings.yookassa_secret_key.get_secret_value()


def _networks(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            networks.append(ipaddress.ip_network(item, strict=False))
    return networks


def notification_source_allowed(source_ip: str | None, *, allowed_cidrs: str | None = None) -> bool:
    """Whether a webhook caller address is inside the configured YooKassa networks.

    An empty allow-list disables the filter (development, tests). A malformed or
    missing address never passes once the filter is on: notifications are only
    ever accepted from the provider, and every accepted one is still confirmed
    by re-reading the payment from the API.
    """
    raw = get_settings().yookassa_webhook_allowed_cidrs if allowed_cidrs is None else allowed_cidrs
    networks = _networks(raw)
    if not networks:
        return True
    if not source_ip:
        return False
    try:
        address = ipaddress.ip_address(source_ip.strip())
    except ValueError:
        return False
    return any(address in network for network in networks)


async def _request(
    method: str,
    path: str,
    *,
    idempotency_key: str | None = None,
    json: dict[str, object] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    headers = {"Accept": "application/json"}
    if idempotency_key:
        headers["Idempotence-Key"] = idempotency_key
    try:
        async with httpx.AsyncClient(
            base_url=settings.yookassa_api_url.rstrip("/"),
            auth=_credentials(),
            timeout=20,
        ) as client:
            response = await client.request(method, path, headers=headers, json=json)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise YooKassaUnavailable("YooKassa API request failed") from exc
    if not isinstance(body, dict):
        raise YooKassaUnavailable("YooKassa returned an invalid response")
    return body


def _receipt(customer_email: str, description: str, amount: str) -> dict[str, object]:
    """The fiscal receipt: one full-payment service line for the whole amount."""
    return {
        "customer": {"email": customer_email},
        "items": [
            {
                "description": description[:128],
                "quantity": "1.00",
                "amount": {"value": amount, "currency": "RUB"},
                "vat_code": get_settings().yookassa_vat_code,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }
        ],
    }


async def create_payment(
    *,
    amount: str,
    description: str,
    return_url: str,
    customer_email: str,
    idempotency_key: str,
    metadata: dict[str, str],
    save_payment_method: bool = False,
) -> dict[str, Any]:
    return await _request(
        "POST",
        "/payments",
        idempotency_key=idempotency_key,
        json={
            "amount": {"value": amount, "currency": "RUB"},
            "capture": True,
            "save_payment_method": save_payment_method,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128],
            "metadata": metadata,
            "receipt": _receipt(customer_email, description, amount),
        },
    )


async def create_recurring_payment(
    *,
    amount: str,
    description: str,
    customer_email: str,
    payment_method_id: str,
    idempotency_key: str,
    metadata: dict[str, str],
) -> dict[str, Any]:
    """Charge a provider-saved method without handling raw card data."""
    return await _request(
        "POST",
        "/payments",
        idempotency_key=idempotency_key,
        json={
            "amount": {"value": amount, "currency": "RUB"},
            "capture": True,
            "payment_method_id": payment_method_id,
            "description": description[:128],
            "metadata": metadata,
            "receipt": _receipt(customer_email, description, amount),
        },
    )


async def get_payment(provider_payment_id: str) -> dict[str, Any]:
    return await _request("GET", f"/payments/{provider_payment_id}")


async def create_refund(
    *,
    provider_payment_id: str,
    amount: str,
    idempotency_key: str,
    customer_email: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Refund a payment in full or in part.

    With YooKassa's own 54-ФЗ solution the refund needs its own fiscal receipt
    («чек возврата»), passed in the same request; it mirrors the sale line of the
    original payment. Callers without a customer email (legacy admin tooling)
    get the bare refund, which the provider accepts only for shops that fiscalise
    elsewhere.
    """
    body: dict[str, object] = {
        "payment_id": provider_payment_id,
        "amount": {"value": amount, "currency": "RUB"},
    }
    if customer_email:
        body["receipt"] = _receipt(
            customer_email, description or "Возврат платежа MAX Studio", amount
        )
    return await _request("POST", "/refunds", idempotency_key=idempotency_key, json=body)
