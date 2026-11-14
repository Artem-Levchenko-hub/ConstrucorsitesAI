import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import SecretStr

from tests.test_app_integrations_api import _max_init_data
from yleum_api.core.crypto import encrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.models.max_integration import MaxIntegration
from yleum_api.models.max_staff_checkout import MaxStaffPaymentAttempt
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.schemas.max_management import OwnerRecordItem, OwnerRecordPublic, ResourceId
from yleum_api.services import max_checkout_receipt, max_management

_NOW = datetime(2026, 11, 14, 14, tzinfo=UTC)


def _decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


@pytest.fixture
def scenario(monkeypatch):
    payment = SimpleNamespace(
        id=uuid4(), project_id=uuid4(), order_id=uuid4(), owner_id=uuid4(),
        actor_id=9_007_199_254_740_993, status="succeeded", is_test=True,
        provider_payment_id="private-synthetic-payment", amount_kopecks=1250,
        currency="RUB", created_at=_NOW - timedelta(minutes=10),
        paid_at=_NOW - timedelta(minutes=4), verified_at=_NOW - timedelta(minutes=4),
    )
    item = SimpleNamespace(catalog_id="meal_pack", quantity=1, unit_price_kopecks=1250, option=None)
    order = SimpleNamespace(
        id=payment.order_id, owner_id=payment.owner_id, actor_id=payment.actor_id,
        created_at=_NOW - timedelta(minutes=11), total_kopecks=1250, currency="RUB",
        items=(item,), contact_email="private-mail@example.invalid",
    )
    read_payment = AsyncMock(return_value=payment)
    read_order = AsyncMock(return_value=order)
    monkeypatch.setattr(max_checkout_receipt, "get_staff_checkout", read_payment)
    monkeypatch.setattr(max_checkout_receipt, "read_staff_checkout_order", read_order)
    monkeypatch.setattr(max_checkout_receipt, "get_settings", lambda: SimpleNamespace(
        jwt_secret=SecretStr("synthetic-only-receipt-test-secret-for-signing"),
    ))

    async def issue():
        return await max_checkout_receipt.issue_checkout_receipt(
            None, payment.project_id, payment.order_id, payment.actor_id, clock=lambda: _NOW,
        )

    return SimpleNamespace(
        payment=payment, order=order, item=item, issue=issue, read_order=read_order,
        read_payment=read_payment, public_key=max_checkout_receipt.checkout_receipt_public_key_pem,
    )


@pytest.mark.parametrize("test", [True, False])
async def test_issues_minimal_receipt_with_pinned_signature_and_expiry(scenario, test):
    scenario.payment.is_test = test
    token = await scenario.issue()
    version, encoded, signature = token.split(".")
    assert version == "v1"
    message = f"{version}.{encoded}".encode("ascii")
    key = load_pem_public_key(scenario.public_key().encode("ascii"))
    key.verify(_decode(signature), message)
    with pytest.raises(InvalidSignature):
        key.verify(_decode(signature), message + b"x")
    iat = int(_NOW.timestamp())
    assert json.loads(_decode(encoded)) == {
        "purpose": "yleum.max.checkout.receipt", "project_id": str(scenario.payment.project_id),
        "actor_id": str(scenario.payment.actor_id), "order_id": str(scenario.payment.order_id),
        "sku": "meal_pack", "quantity": 1, "amount_kopecks": 1250, "currency": "RUB",
        "test": test, "iat": iat, "exp": iat + 300,
    }
    assert token == await scenario.issue()


@pytest.mark.parametrize(("field", "value"), [
    ("status", "pending"), ("status", "canceled"), ("is_test", None), ("is_test", "true"),
    ("provider_payment_id", None), ("verified_at", None), ("paid_at", None),
])
async def test_unverified_payment_does_not_read_order_or_mint_receipt(scenario, field, value):
    setattr(scenario.payment, field, value)
    with pytest.raises(ApiError) as error:
        await scenario.issue()
    assert error.value.status_code == 409
    scenario.read_order.assert_not_awaited()


@pytest.mark.parametrize(("field", "value"), [
    ("currency", "USD"), ("total_kopecks", 1251), ("actor_id", 42),
    ("owner_id", None), ("id", None), ("items", ()),
])
async def test_order_mismatch_fails_without_receipt(scenario, field, value):
    setattr(scenario.order, field, value)
    with pytest.raises(ApiError) as error:
        await scenario.issue()
    assert error.value.status_code == 409


@pytest.mark.parametrize(("field", "value"), [
    ("option", "ignored-option"), ("catalog_id", "a/b"), ("quantity", True),
    ("quantity", 2), ("unit_price_kopecks", 1249),
])
async def test_invalid_order_line_cannot_be_signed(scenario, field, value):
    setattr(scenario.item, field, value)
    with pytest.raises(ApiError) as error:
        await scenario.issue()
    assert error.value.status_code == 409


async def test_changed_authority_after_order_read_does_not_issue_receipt(scenario):
    async def change_owner(*_):
        scenario.payment.owner_id = uuid4()
        return scenario.order

    scenario.read_order.side_effect = change_owner
    with pytest.raises(ApiError) as error:
        await scenario.issue()
    assert error.value.status_code == 409


def test_weak_configuration_cannot_supply_issuer_key(monkeypatch):
    monkeypatch.setattr(max_checkout_receipt, "get_settings", lambda: SimpleNamespace(
        jwt_secret=SecretStr("weak-only"),
    ))
    with pytest.raises(ApiError) as error:
        max_checkout_receipt.checkout_receipt_public_key_pem()
    assert error.value.status_code == 503


async def test_receipt_http_uses_real_max_auth_and_reads_only_the_current_buyers_payment(
    client, db_session, monkeypatch,
):
    project_id, owner_id, order_id = uuid4(), uuid4(), uuid4()
    bot_token = "synthetic-checkout-receipt-bot"
    db_session.add(User(id=owner_id))
    await db_session.flush()
    db_session.add(Project(
        id=project_id, owner_id=owner_id, name="QA", slug="qa-checkout-receipt",
        template="max_miniapp",
    ))
    await db_session.flush()
    db_session.add(MaxIntegration(
        project_id=project_id, owner_id=owner_id, bot_token_enc=encrypt_strong(bot_token),
        webhook_secret_enc=encrypt_strong("synthetic-checkout-receipt-webhook"),
    ))
    payment = MaxStaffPaymentAttempt(
        id=uuid4(), project_id=project_id, owner_id=owner_id, order_id=order_id, actor_id=42,
        binding_id=uuid4(), integration_id=uuid4(), shop_id="synthetic-shop", auth_mode="oauth",
        grant_revision=1, amount_kopecks=1250, currency="RUB", request_body={},
        provider_payment_id="private-synthetic-payment", is_test=True, status="pending",
        created_at=_NOW - timedelta(minutes=2),
    )
    db_session.add(payment)
    await db_session.commit()
    record = OwnerRecordPublic(
        id=order_id, resource=ResourceId.orders, created_at=_NOW - timedelta(minutes=3),
        revision="a" * 64, status="new", customer_name="Synthetic private buyer",
        contact_email="private@example.invalid", total_kopecks=1250, currency="RUB",
        items=[OwnerRecordItem(
            catalog_id="meal_pack", title="Private basket title", unit_price_kopecks=1250,
            quantity=1,
        )],
    )
    remote_order = AsyncMock(return_value=record)
    monkeypatch.setattr(max_management, "get_record", remote_order)
    monkeypatch.setattr(max_checkout_receipt, "get_settings", lambda: SimpleNamespace(
        jwt_secret=SecretStr("synthetic-only-receipt-test-secret-for-signing"),
    ))
    route = f"/api/runtime/projects/{project_id}/orders/checkout/receipt"
    buyer = {"X-MAX-Init-Data": _max_init_data(bot_token, 42)}
    other = {"X-MAX-Init-Data": _max_init_data(bot_token, 43)}
    body = {"order_id": str(order_id)}
    unauthorized = await client.post(route, json=body)
    assert unauthorized.status_code == 401
    pending = await client.post(route, json=body, headers=buyer)
    assert pending.status_code == 409
    remote_order.assert_not_awaited()

    payment.status = "succeeded"
    payment.paid_at = payment.verified_at = _NOW - timedelta(minutes=1)
    await db_session.commit()
    foreign = await client.post(route, json=body, headers=other)
    assert foreign.status_code == 404
    remote_order.assert_not_awaited()
    forged = await client.post(route, json={**body, "sku": "other"}, headers=buyer)
    assert forged.status_code == 422
    remote_order.assert_not_awaited()

    receipt = await client.post(route, json=body, headers=buyer)
    assert receipt.status_code == 200, receipt.text
    assert receipt.headers["cache-control"] == "private, no-store"
    data = receipt.json()
    assert set(data) == {"receipt"}
    claims = json.loads(_decode(data["receipt"].split(".")[1]))
    assert claims["sku"] == "meal_pack" and claims["actor_id"] == "42" and claims["test"] is True
    assert claims["order_id"] == str(order_id) and claims["project_id"] == str(project_id)
    assert claims["amount_kopecks"] == 1250 and claims["currency"] == "RUB"
    remote_order.assert_awaited_once_with(
        db_session, project_id, owner_id, order_id, ResourceId.orders, actor="42",
    )
