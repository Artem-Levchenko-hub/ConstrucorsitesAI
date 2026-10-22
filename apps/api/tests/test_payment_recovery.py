"""Provider acceptance can outlive a lost response; never invent a second charge."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from tests.account_fixture import register_verified_account
from tests.test_subscription_lifecycle import _registered_billing_context
from yleum_api.core.config import get_settings
from yleum_api.models.account import Payment
from yleum_api.models.billing import PRO_PLAN_ID, BillingPaymentMethod, Subscription
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.routers import payments as payments_router
from yleum_api.services import yookassa
from yleum_api.services.payment_state import reconcile_pending_payments
from yleum_api.services.subscription_lifecycle import process_subscription_cycle


@pytest.fixture
def accepted_then_lost(monkeypatch):
    """Fake only the provider boundary; persistence and settlement remain real."""
    calls = []
    accepted = {}

    async def provider(method, path, *, idempotency_key=None, json=None):
        assert method == "POST" and path == "/payments"
        key = idempotency_key
        calls.append((key, deepcopy(json)))
        if key not in accepted:
            accepted[key] = {
                "id": f"accepted-{len(accepted) + 1}",
                "status": "pending",
                "amount": deepcopy(json["amount"]),
                "metadata": deepcopy(json["metadata"]),
                "confirmation": {"confirmation_url": "https://yookassa.test/pay"},
            }
            raise yookassa.YooKassaUnavailable("accepted before the response was lost")
        assert json == calls[0][1], "the original request, including its receipt, must survive"
        return deepcopy(accepted[key])

    monkeypatch.setattr(yookassa, "_request", provider)
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    return calls, accepted


@pytest.mark.parametrize("purpose", ["wallet_topup", "subscription_initial"])
async def test_timeout_retry_recovers_original_checkout(
    client,
    db_session,
    monkeypatch,
    accepted_then_lost,
    purpose,
):
    calls, accepted = accepted_then_lost
    await register_verified_account(client, "lost-checkout@example.com")
    path = "/api/payments/subscription" if purpose == "subscription_initial" else "/api/payments"
    body = {"idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be"}
    body.update(
        {"plan_code": "pro", "plan_version": 3}
        if purpose == "subscription_initial"
        else {"package_code": "start"}
    )
    first = await client.post(path, json=body)
    assert first.status_code == 503
    local = (await db_session.execute(select(Payment))).scalar_one()
    assert local.status == "pending"
    if local.subscription_id:
        subscription = await db_session.get(Subscription, local.subscription_id)
        assert subscription.status == "pending_payment"

    # A configuration change between attempts must not alter the old fiscal receipt.
    monkeypatch.setattr(
        yookassa,
        "get_settings",
        lambda: get_settings().model_copy(
            update={"yookassa_vat_code": 11},
        ),
    )
    second = await client.post(path, json=body)
    assert second.status_code == 201
    assert second.json()["id"] == str(local.id)
    await db_session.refresh(local)
    assert local.provider_payment_id == "accepted-1"
    assert second.json()["confirmation_url"] == "https://yookassa.test/pay"
    assert len(accepted) == 1 and len(calls) == 2
    assert calls[0] == calls[1]
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 1


async def test_verified_webhook_recovers_missing_provider_id_and_credits_once(
    client,
    db_session,
    monkeypatch,
    accepted_then_lost,
):
    _, accepted = accepted_then_lost
    await register_verified_account(client, "lost-webhook@example.com")
    first = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "plan_version": 3,
            "idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be",
        },
    )
    assert first.status_code == 503
    local = (await db_session.execute(select(Payment))).scalar_one()
    provider = next(iter(accepted.values())) | {"status": "succeeded"}

    async def verified(provider_id):
        assert provider_id == "accepted-1"
        return deepcopy(provider)

    monkeypatch.setattr(yookassa, "get_payment", verified)
    event = {"event": "payment.succeeded", "object": {"id": "accepted-1"}}
    for _ in range(2):
        reply = await client.post("/api/payments/yookassa/webhook", json=event)
        assert reply.status_code == 200
    await db_session.refresh(local)
    assert local.status == "succeeded" and local.provider_payment_id == "accepted-1"
    active = await client.get("/api/billing/subscription")
    assert active.json()["plan"]["code"] == "pro"
    assert await db_session.scalar(
        select(func.count())
        .select_from(WalletCharge)
        .where(
            WalletCharge.external_ref == f"subscription-credit:{local.id}",
        )
    ) == (1 if local.credit_rub > 0 else 0)


async def test_background_recovers_uncertain_checkout_but_never_reposts_after_24_hours(
    client,
    db_session,
    accepted_then_lost,
):
    calls, accepted = accepted_then_lost
    await register_verified_account(client, "sweep-checkout@example.com")
    await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "plan_version": 3,
            "idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be",
        },
    )
    local = (await db_session.execute(select(Payment))).scalar_one()
    await reconcile_pending_payments(db_session, now=datetime.now(UTC) + timedelta(minutes=20))
    await db_session.refresh(local)
    assert local.provider_payment_id == "accepted-1"
    assert len(calls) == 2 and len(accepted) == 1


async def test_expired_uncertain_order_stays_open_without_a_new_charge(
    client,
    db_session,
    accepted_then_lost,
):
    calls, _ = accepted_then_lost
    await register_verified_account(client, "expired-checkout@example.com")
    await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "plan_version": 3,
            "idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be",
        },
    )
    local = (await db_session.execute(select(Payment))).scalar_one()
    await reconcile_pending_payments(db_session, now=datetime.now(UTC) + timedelta(hours=25))
    await db_session.refresh(local)
    assert local.status == "pending" and local.provider_payment_id is None
    assert len(calls) == 1
    new_checkout = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "plan_version": 3,
            "idempotency_key": "7f0e3500-7033-4080-917e-d8564fa30170",
        },
    )
    assert new_checkout.status_code == 409


async def test_lost_renewal_response_reuses_one_charge_and_one_credit(
    client,
    db_session,
    monkeypatch,
    accepted_then_lost,
):
    calls, accepted = accepted_then_lost
    user, account, free = await _registered_billing_context(
        client,
        db_session,
        email="lost-renewal@example.com",
    )
    now = datetime.now(UTC).replace(microsecond=0)
    free.status = "expired"
    free.ended_at = now
    await db_session.flush()
    method = BillingPaymentMethod(
        billing_account_id=account.id,
        user_id=user.id,
        provider="yookassa",
        provider_payment_method_id="saved-method",
        status="active",
        consent_version=get_settings().legal_document_version,
        consented_at=now,
    )
    db_session.add(method)
    await db_session.flush()
    subscription = Subscription(
        billing_account_id=account.id,
        user_id=user.id,
        plan_id=PRO_PLAN_ID,
        payment_method_id=method.id,
        status="active",
        auto_renew=True,
        current_period_start=now - timedelta(days=30),
        current_period_end=now,
        next_charge_at=now,
        renewal_consent_version=get_settings().legal_document_version,
        renewal_consented_at=now,
    )
    db_session.add(subscription)
    await db_session.commit()
    assert await process_subscription_cycle(db_session, now=now) == 1
    local = (await db_session.execute(select(Payment))).scalar_one()
    assert local.status == "pending"
    assert len(calls[0][0]) <= 64, "YooKassa rejects keys longer than 64 characters"
    next(iter(accepted.values()))["status"] = "succeeded"
    retry_at = now + timedelta(hours=get_settings().billing_renewal_retry_hours, seconds=1)
    assert await process_subscription_cycle(db_session, now=retry_at) == 1
    await db_session.refresh(subscription)
    await db_session.refresh(local)
    assert local.status == "succeeded"
    assert subscription.status == "active" and subscription.current_period_end > retry_at
    assert len(calls) == 2 and calls[0] == calls[1] and len(accepted) == 1
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(WalletCharge)
        .where(
            WalletCharge.external_ref == f"subscription-credit:{local.id}",
        )
    ) == (1 if local.credit_rub > 0 else 0)


@pytest.mark.parametrize("purpose", ["wallet_topup", "subscription_initial"])
async def test_late_success_cannot_resurrect_a_refunded_payment(
    client,
    db_session,
    monkeypatch,
    accepted_then_lost,
    purpose,
):
    _, accepted = accepted_then_lost
    await register_verified_account(client, "refunded-order@example.com")
    path = "/api/payments/subscription" if purpose == "subscription_initial" else "/api/payments"
    body = {"idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be"}
    body.update(
        {"plan_code": "pro", "plan_version": 3}
        if purpose == "subscription_initial"
        else {"package_code": "start"}
    )
    await client.post(path, json=body)
    local = (await db_session.execute(select(Payment))).scalar_one()
    provider = next(iter(accepted.values())) | {"status": "succeeded"}

    async def verified(_provider_id):
        return deepcopy(provider)

    monkeypatch.setattr(yookassa, "get_payment", verified)
    event = {"event": "payment.succeeded", "object": {"id": "accepted-1"}}
    assert (await client.post("/api/payments/yookassa/webhook", json=event)).status_code == 200
    await db_session.refresh(local)
    local.status = "refunded"
    local.refunded_at = datetime.now(UTC)
    await db_session.commit()
    replay = await client.post("/api/payments/yookassa/webhook", json=event)
    assert replay.status_code == 200
    await db_session.refresh(local)
    assert local.status == "refunded"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(WalletCharge)
            .where(
                WalletCharge.entry_type.in_(("subscription_credit", "payment")),
            )
        )
        == (1 if purpose == "wallet_topup" else 0)
    )


async def test_rejected_retry_does_not_disprove_the_earlier_uncertain_charge(
    client,
    db_session,
    monkeypatch,
    accepted_then_lost,
):
    await register_verified_account(client, "rejected-retry@example.com")
    body = {
        "plan_code": "pro",
        "plan_version": 3,
        "idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be",
    }
    assert (await client.post("/api/payments/subscription", json=body)).status_code == 503

    async def rejected(*_args, **_kwargs):
        # Credentials may be revoked after the original charge was accepted.
        raise yookassa.YooKassaUnavailable("current credentials rejected", definitive=True)

    monkeypatch.setattr(yookassa, "_request", rejected)
    assert (await client.post("/api/payments/subscription", json=body)).status_code == 503
    local = (await db_session.execute(select(Payment))).scalar_one()
    assert local.status == "pending"
    new_order = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "plan_version": 3,
            "idempotency_key": "7f0e3500-7033-4080-917e-d8564fa30170",
        },
    )
    assert new_order.status_code == 409


async def test_manual_review_orders_do_not_starve_newer_recovery(
    client,
    db_session,
    accepted_then_lost,
):
    calls, _ = accepted_then_lost
    await register_verified_account(client, "fair-recovery@example.com")
    await client.post(
        "/api/payments",
        json={
            "package_code": "start",
            "idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be",
        },
    )
    local = (await db_session.execute(select(Payment))).scalar_one()
    for _ in range(50):
        db_session.add(
            Payment(
                billing_account_id=local.billing_account_id,
                user_id=local.user_id,
                idempotency_key=str(uuid4()),
                purpose="wallet_topup",
                package_code="start",
                amount_rub=490,
                credit_rub=500,
                status="pending",
                created_at=datetime.now(UTC) - timedelta(days=90),
            )
        )
    await db_session.commit()
    for _ in range(2):
        await reconcile_pending_payments(db_session, now=datetime.now(UTC) + timedelta(minutes=20))
    await db_session.refresh(local)
    assert local.provider_payment_id == "accepted-1"
    assert len(calls) == 2
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Payment)
            .where(
                Payment.status == "pending",
            )
        )
        == 51
    )


@pytest.mark.parametrize("purpose", ["wallet_topup", "subscription_initial"])
async def test_original_request_is_durable_in_the_first_order_commit(
    client,
    db_session,
    accepted_then_lost,
    purpose,
):
    await register_verified_account(client, "atomic-order@example.com")
    committed_without_request = []

    def observe_commit(session):
        for row in session.identity_map.values():
            if isinstance(row, Payment) and row.status == "pending" and not row.provider_payment_id:
                if not isinstance((row.provider_payload or {}).get("_submission"), dict):
                    committed_without_request.append(str(row.id))

    event.listen(db_session.sync_session, "after_commit", observe_commit)
    try:
        path = (
            "/api/payments/subscription" if purpose == "subscription_initial" else "/api/payments"
        )
        body = {"idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be"}
        body.update(
            {"plan_code": "pro", "plan_version": 3}
            if purpose == "subscription_initial"
            else {"package_code": "start"}
        )
        response = await client.post(path, json=body)
        assert response.status_code == 503
        assert committed_without_request == [], "a crash here would strand an unsubmitted order"
    finally:
        event.remove(db_session.sync_session, "after_commit", observe_commit)


async def test_refund_is_blocked_until_manual_provider_and_entitlement_review(
    client,
    monkeypatch,
):
    await register_verified_account(client, "manual-refund@example.com")
    monkeypatch.setattr(payments_router, "_configured", lambda: True)

    async def immediate_payment(**kwargs):
        return {
            "id": "paid-manual-refund",
            "status": "succeeded",
            "amount": {"value": "490.00", "currency": "RUB"},
            "metadata": kwargs["metadata"],
        }

    calls = []

    async def pending_refund(**kwargs):
        calls.append(kwargs)
        return {"id": "unconfirmed-refund", "status": "pending"}

    monkeypatch.setattr(yookassa, "create_payment", immediate_payment)
    monkeypatch.setattr(yookassa, "create_refund", pending_refund)
    created = await client.post(
        "/api/payments",
        json={
            "package_code": "start",
            "idempotency_key": "77dff9ba-29be-4876-9ea5-10bb7a3b69be",
        },
    )
    assert created.status_code == 201 and created.json()["status"] == "succeeded"
    payment_id = created.json()["id"]
    path = f"/api/payments/{payment_id}/refund"
    assert (await client.post(path)).status_code == 403
    monkeypatch.setattr(payments_router, "_is_admin", lambda user: True)
    response = await client.post(path)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "refund_unavailable"
    assert calls == []
    assert (await client.get("/api/wallet")).json()["balance_rub"] == "600.0000"
    assert (await client.get("/api/payments")).json()[0]["status"] == "succeeded"
