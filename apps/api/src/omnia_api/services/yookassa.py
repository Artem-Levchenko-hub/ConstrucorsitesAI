from __future__ import annotations

from typing import Any

import httpx

from omnia_api.core.config import get_settings


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
    settings = get_settings()
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
            "receipt": {
                "customer": {"email": customer_email},
                "items": [
                    {
                        "description": description[:128],
                        "quantity": "1.00",
                        "amount": {"value": amount, "currency": "RUB"},
                        "vat_code": settings.yookassa_vat_code,
                        "payment_mode": "full_payment",
                        "payment_subject": "service",
                    }
                ],
            },
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
    settings = get_settings()
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
            "receipt": {
                "customer": {"email": customer_email},
                "items": [
                    {
                        "description": description[:128],
                        "quantity": "1.00",
                        "amount": {"value": amount, "currency": "RUB"},
                        "vat_code": settings.yookassa_vat_code,
                        "payment_mode": "full_payment",
                        "payment_subject": "service",
                    }
                ],
            },
        },
    )


async def get_payment(provider_payment_id: str) -> dict[str, Any]:
    return await _request("GET", f"/payments/{provider_payment_id}")


async def create_refund(
    *,
    provider_payment_id: str,
    amount: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return await _request(
        "POST",
        "/refunds",
        idempotency_key=idempotency_key,
        json={
            "payment_id": provider_payment_id,
            "amount": {"value": amount, "currency": "RUB"},
        },
    )
