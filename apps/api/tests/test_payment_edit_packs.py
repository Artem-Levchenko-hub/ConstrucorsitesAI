"""Frozen count-credit quotes and provider-confirmed settlement routing."""

from datetime import UTC, datetime
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from yleum_api.models.billing import DEFAULT_BILLING_PLANS
from yleum_api.models.user import User
from yleum_api.routers import payments
from yleum_api.schemas.payments import PaymentCreate
from yleum_api.services import payment_state, payment_submission


def test_new_plan_versions_preserve_prior_contracts():
    latest = {plan["code"]: plan for plan in DEFAULT_BILLING_PLANS if plan["is_active"]}
    assert [latest[code]["version"] for code in ("free", "pro", "business")] == [4, 5, 3]
    assert latest["pro"]["price_rub"] == Decimal("1.00")
    assert all(plan["included_credit_rub"] == 0 for plan in latest.values())
    assert latest["free"]["entitlements"]["initial_generations_lifetime"] == 1
    assert latest["free"]["entitlements"]["edits_lifetime"] == 1
    assert "generation_messages_lifetime" not in latest["free"]["entitlements"]
    assert latest["pro"]["entitlements"]["initial_generations_monthly"] == 2
    assert latest["pro"]["entitlements"]["edits_monthly"] == 25
    assert latest["pro"]["entitlements"]["always_on_slots"] == 1
    assert latest["business"]["entitlements"]["initial_generations_monthly"] == 5
    assert latest["business"]["entitlements"]["edits_monthly"] == 50
    assert [
        latest[code]["entitlements"]["max_projects"] for code in ("free", "pro", "business")
    ] == [1, 3, 10]
    old = next(
        plan for plan in DEFAULT_BILLING_PLANS if plan["code"] == "pro" and plan["version"] == 2
    )
    assert old["price_rub"] == Decimal("2990.00")
    assert old["included_credit_rub"] == Decimal("500.0000")
    assert old["is_active"] is False
    prior_count_plan = next(
        plan for plan in DEFAULT_BILLING_PLANS if plan["code"] == "pro" and plan["version"] == 3
    )
    assert prior_count_plan["price_rub"] == Decimal("1.00")
    assert prior_count_plan["is_active"] is False


async def test_package_config_reports_real_units_and_restrictions(monkeypatch):
    monkeypatch.setattr(payments, "_configured", lambda: True)
    result = (await payments.get_payment_config()).model_dump(mode="json")
    by_code = {row["code"]: row for row in result["packages"]}
    assert [
        (by_code[code]["price_rub"], by_code[code]["edit_units"], by_code[code]["edits_only"])
        for code in ("start", "business", "pro")
    ] == [("490.00", 5, True), ("1490.00", 15, False), ("3990.00", 40, False)]
    assert all(
        row["credit_rub"] == "0.00" for row in by_code.values()
    )
    assert [by_code[code]["package_version"] for code in ("start", "business", "pro")] == [3, 2, 2]


async def test_order_freezes_count_quote_and_replay_uses_saved_order(monkeypatch):
    monkeypatch.setattr(payments, "_configured", lambda: True)
    account = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(payments, "resolve_billing_account", AsyncMock(return_value=account))
    saved = []
    session = SimpleNamespace(
        add=saved.append,
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
    )
    user = User(
        id=uuid4(), email="synthetic@example.invalid", is_anon=False, status="active",
        phone_e164="+79991234567", phone_verified_at=datetime.now(UTC),
    )
    payload = PaymentCreate(package_code="start", idempotency_key=uuid4())

    async def send(**kwargs):
        return kwargs["payment"]

    monkeypatch.setattr(payments, "_send_to_provider", send)
    order = await payments.create_payment(payload, user, session)
    assert (
        order.amount_rub,
        order.credit_rub,
        order.edit_units,
        order.edits_only,
        order.package_version,
    ) == (Decimal("490.00"), Decimal("0.00"), 5, True, 3)
    session.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: order)
    monkeypatch.setattr(payments, "PACKAGES", {})
    monkeypatch.setattr(payments, "_resume_checkout", send)
    assert await payments.create_payment(payload, user, session) is order
    assert len(saved) == 1


async def test_pre_cutover_starter_checkout_replays_its_one_ruble_quote(monkeypatch):
    monkeypatch.setattr(payments, "_configured", lambda: True)
    order = quote()
    monkeypatch.setattr(
        payments, "resolve_billing_account", AsyncMock(return_value=SimpleNamespace(
            id=order.billing_account_id,
        )),
    )
    monkeypatch.setattr(payments, "_resume_checkout", AsyncMock(return_value=order))
    user = User(
        id=order.user_id, email="synthetic@example.invalid", is_anon=False, status="active",
        phone_e164="+79991234567", phone_verified_at=datetime.now(UTC),
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: order)),
    )
    result = await payments.create_payment(
        PaymentCreate(package_code="start", idempotency_key=order.idempotency_key), user, session,
    )
    assert (result.amount_rub, result.package_version, result.edit_units) == (Decimal("1.00"), 2, 5)


def quote(**changes):
    return SimpleNamespace(
        id=uuid4(),
        billing_account_id=uuid4(),
        user_id=uuid4(),
        idempotency_key=str(uuid4()),
        purpose="wallet_topup",
        package_code="start",
        amount_rub=Decimal("1.00"),
        credit_rub=Decimal("0.00"),
        status="pending",
        provider_payment_id=None,
        provider_payload={},
        edit_units=5,
        edits_only=True,
        package_version=2,
        **changes,
    )


def test_saved_provider_request_binds_frozen_unit_snapshot():
    order = quote()
    payment_submission.prepare_submission(
        order,
        customer_email="synthetic@example.invalid",
        title="Five edits",
        return_url="https://example.invalid/return",
    )
    saved = order.provider_payload["_submission"]["arguments"]
    assert saved["metadata"]["edit_units"] == "5"
    assert saved["metadata"]["edits_only"] == "true"
    assert saved["metadata"]["package_version"] == "2"
    payment_submission.prepare_submission(
        order,
        customer_email="changed@example.invalid",
        title="Changed",
        return_url="https://example.invalid/changed",
    )
    assert order.provider_payload["_submission"]["arguments"] == saved


async def test_verified_pack_grants_once_without_ruble_wallet_mutation(monkeypatch):
    module = ModuleType("yleum_api.services.generation_credits")

    async def verified_grant(_session, payment):
        assert payment.status == "succeeded" and payment.paid_at is not None

    grant = AsyncMock(side_effect=verified_grant)
    module.grant_payment_units = grant
    monkeypatch.setitem(__import__("sys").modules, module.__name__, module)
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=AssertionError("pack must not use RUB wallet"))
    )
    order = quote()
    provider = {
        "id": "synthetic-provider",
        "amount": {"value": "1.00", "currency": "RUB"},
        "metadata": {"payment_id": str(order.id)},
        "status": "waiting_for_capture",
    }
    await payment_state.apply_provider_state(session, order, provider)
    grant.assert_not_awaited()
    provider["status"] = "succeeded"
    await payment_state.apply_provider_state(session, order, provider)
    await payment_state.apply_provider_state(session, order, provider)
    grant.assert_awaited_once_with(session, order)
    assert order.status == "succeeded"


async def test_historical_ruble_quote_keeps_original_wallet_credit():
    order = quote()
    order.edit_units, order.edits_only, order.package_version = 0, False, None
    order.amount_rub, order.credit_rub = Decimal("490.00"), Decimal("500.00")
    wallet = SimpleNamespace(balance_rub=Decimal("100.00"))
    entries = []
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: wallet)),
        add=entries.append,
    )
    provider = {
        "id": "old-provider",
        "amount": {"value": "490.00", "currency": "RUB"},
        "metadata": {"payment_id": str(order.id)},
        "status": "succeeded",
    }
    await payment_state.apply_provider_state(session, order, provider)
    await payment_state.apply_provider_state(session, order, provider)
    assert wallet.balance_rub == Decimal("600.00")
    assert len(entries) == 1 and entries[0].external_ref == f"payment:{order.id}"
