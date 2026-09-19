"""Characterisation of what the YooKassa client actually sends.

Every other payment test replaces ``create_payment`` wholesale, so the request
body — including the fiscal receipt the provider forwards to the tax service —
was pinned nowhere. Frozen BEFORE the receipt got one owner, unchanged AFTER.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from omnia_api.services import yookassa

LONG = "Подписка MAX Studio: " + "очень длинное описание тарифа " * 8


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def record(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"method": method, "path": path, **kwargs})
        return {"id": "provider-payment"}

    monkeypatch.setattr(yookassa, "_request", record)
    monkeypatch.setattr(yookassa, "get_settings", lambda: SimpleNamespace(yookassa_vat_code=6))
    return calls


def receipt(description: str, amount: str, email: str) -> dict[str, Any]:
    return {
        "customer": {"email": email},
        "items": [
            {
                "description": description,
                "quantity": "1.00",
                "amount": {"value": amount, "currency": "RUB"},
                "vat_code": 6,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }
        ],
    }


@pytest.mark.parametrize("save", [False, True])
async def test_first_payment_request(sent, save):
    result = await yookassa.create_payment(
        amount="990.00",
        description=LONG,
        return_url="https://app.example.test/billing/return",
        customer_email="owner@example.test",
        idempotency_key="key-1",
        metadata={"payment_id": "p1"},
        **({"save_payment_method": True} if save else {}),
    )

    assert result == {"id": "provider-payment"}
    assert sent == [
        {
            "method": "POST",
            "path": "/payments",
            "idempotency_key": "key-1",
            "json": {
                "amount": {"value": "990.00", "currency": "RUB"},
                "capture": True,
                "save_payment_method": save,
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://app.example.test/billing/return",
                },
                "description": LONG[:128],
                "metadata": {"payment_id": "p1"},
                "receipt": receipt(LONG[:128], "990.00", "owner@example.test"),
            },
        }
    ]
    assert list(sent[0]["json"]) == [
        "amount", "capture", "save_payment_method", "confirmation", "description",
        "metadata", "receipt",
    ]


async def test_recurring_payment_request(sent):
    await yookassa.create_recurring_payment(
        amount="990.00",
        description=LONG,
        customer_email="owner@example.test",
        payment_method_id="saved-method",
        idempotency_key="key-2",
        metadata={"subscription_id": "s1"},
    )

    assert sent == [
        {
            "method": "POST",
            "path": "/payments",
            "idempotency_key": "key-2",
            "json": {
                "amount": {"value": "990.00", "currency": "RUB"},
                "capture": True,
                "payment_method_id": "saved-method",
                "description": LONG[:128],
                "metadata": {"subscription_id": "s1"},
                "receipt": receipt(LONG[:128], "990.00", "owner@example.test"),
            },
        }
    ]
    assert list(sent[0]["json"]) == [
        "amount", "capture", "payment_method_id", "description", "metadata", "receipt",
    ]


async def test_refund_and_lookup_requests(sent):
    await yookassa.create_refund(
        provider_payment_id="provider-payment", amount="100.00", idempotency_key="key-3"
    )
    await yookassa.get_payment("provider-payment")

    assert sent == [
        {
            "method": "POST",
            "path": "/refunds",
            "idempotency_key": "key-3",
            "json": {
                "payment_id": "provider-payment",
                "amount": {"value": "100.00", "currency": "RUB"},
            },
        },
        {"method": "GET", "path": "/payments/provider-payment"},
    ]
