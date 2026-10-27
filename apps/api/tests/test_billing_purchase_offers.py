"""An authorized one-ruble payment/email test never changes product entitlements."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_session
from yleum_api.core.errors import ApiError, api_error_handler
from yleum_api.models.account import Payment
from yleum_api.models.base import Base
from yleum_api.models.billing import PRO_PLAN_ID, PRO_PLAN_V3_ID, BillingPaymentMethod
from yleum_api.models.email_notification import EmailNotification
from yleum_api.models.generation_credits import GenerationCreditGrant
from yleum_api.models.user import User
from yleum_api.routers import payments as payments_router
from yleum_api.services import yookassa
from yleum_api.services.payment_state import apply_provider_state, reconcile_pending_payments

pytestmark = pytest.mark.asyncio


async def test_guest_cannot_start_payment_test():
    async def no_database():
        yield None

    app = FastAPI()
    app.dependency_overrides[get_session] = no_database
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(payments_router.router)
    with TestClient(app, raise_server_exceptions=False) as guest:
        assert (
            guest.post(
                "/api/payments/test",
                json={
                    "offer_id": str(uuid4()),
                    "idempotency_key": str(uuid4()),
                },
            ).status_code
            == 401
        )


async def _grant(client, session, *, plan_id=PRO_PLAN_ID, auto_renew=True):
    from tests.test_subscription_lifecycle import _registered_billing_context
    from yleum_api.services.billing_offers import issue_billing_purchase_offer

    user, account, subscription = await _registered_billing_context(
        client, session, email="personal-test-payment@example.com"
    )
    now = datetime.now(UTC)
    user.role = "admin"
    subscription.plan_id = plan_id
    subscription.current_period_start = now - timedelta(days=10)
    subscription.current_period_end = now + timedelta(days=20)
    subscription.auto_renew = auto_renew
    subscription.next_charge_at = subscription.current_period_end if auto_renew else None
    if auto_renew:
        method = BillingPaymentMethod(
            billing_account_id=account.id,
            user_id=user.id,
            provider_payment_method_id="existing-consented-method",
            status="active",
            consent_version=get_settings().legal_document_version,
            consented_at=now,
        )
        session.add(method)
        await session.flush()
        subscription.payment_method_id = method.id
        subscription.renewal_consent_version = method.consent_version
        subscription.renewal_consented_at = now
    session.add(
        GenerationCreditGrant(
            billing_account_id=account.id,
            bucket="edit",
            scope="free",
            units=3,
            external_ref=f"fixture:{account.id}",
        )
    )
    await session.commit()
    before = await _product_state(session)
    offer = await issue_billing_purchase_offer(session, user_id=user.id, issued_by_user_id=user.id)
    await session.commit()
    assert await _product_state(session) == before
    return user, account, offer


async def _product_state(session):
    state = {}
    for name in (
        "subscriptions",
        "billing_plans",
        "billing_payment_methods",
        "wallets",
        "wallet_charges",
        "generation_credit_grants",
        "generation_credit_reservations",
    ):
        table = Base.metadata.tables[name]
        rows = (
            await session.execute(select(table).order_by(*table.primary_key.columns))
        ).mappings()
        state[name] = [dict(row) for row in rows]
    return state


@pytest.fixture
def provider(monkeypatch):
    calls = []

    async def create(**kwargs):
        calls.append(deepcopy(kwargs))
        return {
            "id": f"test-provider-{len(calls)}",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/pay"},
        }

    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    monkeypatch.setattr(yookassa, "create_payment", create)
    return calls


async def _checkout(client, offer, **extra):
    return await client.post(
        "/api/payments/test",
        json={
            "offer_id": str(offer.id),
            "idempotency_key": str(uuid4()),
            **extra,
        },
    )


def _state(payment, status="succeeded", amount="1.00", currency="RUB"):
    return {
        "id": payment.provider_payment_id,
        "status": status,
        "amount": {"value": amount, "currency": currency},
        "metadata": {"payment_id": str(payment.id)},
    }


@pytest.mark.parametrize("plan_id", [PRO_PLAN_ID, PRO_PLAN_V3_ID])
@pytest.mark.parametrize("auto_renew", [True, False])
async def test_success_and_replay_preserve_subscription_credits_and_only_queue_test_email(
    client, db_session, provider, plan_id, auto_renew
):
    _, _, offer = await _grant(client, db_session, plan_id=plan_id, auto_renew=auto_renew)
    before = await _product_state(db_session)
    quote = (await client.get("/api/billing/purchase-offer")).json()
    assert quote["price_rub"] == "1.00"
    public = (await client.get("/api/billing/plans")).json()
    assert next(p for p in public if p["code"] == "pro")["price_rub"] == "2990.00"
    created = await _checkout(client, offer)
    assert created.status_code == 201, created.text
    payment = await db_session.get(Payment, UUID(created.json()["id"]))
    assert payment.purpose == "payment_test" and payment.subscription_id is None
    assert payment.amount_rub == Decimal("1.00") and payment.credit_rub == 0
    assert provider[0]["amount"] == "1.00"
    assert provider[0]["receipt"]["items"][0]["amount"]["value"] == "1.00"
    assert provider[0]["save_payment_method"] is False
    assert await _product_state(db_session) == before
    await apply_provider_state(db_session, payment, _state(payment))
    await db_session.commit()
    await apply_provider_state(db_session, payment, _state(payment))
    await db_session.commit()
    assert await _product_state(db_session) == before
    await db_session.refresh(offer)
    assert offer.consumed_payment_id == payment.id
    notices = (await db_session.scalars(select(EmailNotification))).all()
    assert len(notices) == 1 and notices[0].event_type == "payment_test"
    assert notices[0].amount_rub == Decimal("1.00")
    assert notices[0].subscription_id is None and notices[0].period_end is None
    assert (await client.get("/api/billing/purchase-offer")).json() is None
    assert (await _checkout(client, offer)).status_code == 409
    assert len(provider) == 1


async def test_definitive_decline_can_retry_but_never_changes_current_product(
    client, db_session, provider
):
    _, _, offer = await _grant(client, db_session)
    before = await _product_state(db_session)
    key = str(uuid4())
    first = await _checkout(client, offer, idempotency_key=key)
    payment = await db_session.get(Payment, UUID(first.json()["id"]))
    await apply_provider_state(db_session, payment, _state(payment, "canceled"))
    await db_session.commit()
    assert await _product_state(db_session) == before
    second = await _checkout(client, offer)
    assert second.status_code == 201 and second.json()["id"] != first.json()["id"]
    replay = await _checkout(client, offer, idempotency_key=key)
    assert replay.json()["id"] == first.json()["id"] and replay.json()["status"] == "cancelled"
    assert len(provider) == 2
    assert await _product_state(db_session) == before


async def test_ambiguous_provider_outcome_reuses_the_exact_submission(
    client, db_session, monkeypatch
):
    _, _, offer = await _grant(client, db_session)
    before = await _product_state(db_session)
    calls = []

    async def lost(**kwargs):
        calls.append(deepcopy(kwargs))
        raise yookassa.YooKassaUnavailable("accepted before response lost")

    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    monkeypatch.setattr(yookassa, "create_payment", lost)
    assert (await _checkout(client, offer)).status_code == 503
    assert (await _checkout(client, offer)).status_code == 503
    assert len(calls) == 2 and calls[0] == calls[1]
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 1
    assert await _product_state(db_session) == before


async def test_other_account_cannot_read_or_use_the_grant(client, db_session, provider):
    from tests.test_subscription_lifecycle import _registered_billing_context

    _, _, offer = await _grant(client, db_session)
    await _registered_billing_context(client, db_session, email="other-test-payment@example.com")
    assert (await client.get("/api/billing/purchase-offer")).json() is None
    assert (await _checkout(client, offer)).status_code == 404
    assert provider == []


@pytest.mark.parametrize("amount,currency", [("0.01", "RUB"), ("1.00", "USD")])
async def test_invalid_provider_amount_or_currency_never_consumes_offer(
    client, db_session, provider, amount, currency
):
    _, _, offer = await _grant(client, db_session)
    before = await _product_state(db_session)
    created = await _checkout(client, offer)
    payment = await db_session.get(Payment, UUID(created.json()["id"]))
    with pytest.raises(ApiError):
        await apply_provider_state(
            db_session, payment, _state(payment, amount=amount, currency=currency)
        )
    await db_session.rollback()
    await db_session.refresh(payment)
    await db_session.refresh(offer)
    assert payment.status == "pending" and offer.consumed_at is None
    assert await _product_state(db_session) == before


async def test_consumption_payment_and_notice_roll_back_together(client, db_session, provider):
    _, _, offer = await _grant(client, db_session)
    before = await _product_state(db_session)
    created = await _checkout(client, offer)
    payment = await db_session.get(Payment, UUID(created.json()["id"]))
    await apply_provider_state(db_session, payment, _state(payment))
    await db_session.rollback()
    await db_session.refresh(payment)
    await db_session.refresh(offer)
    assert payment.status == "pending" and offer.consumed_at is None
    assert await db_session.scalar(select(func.count()).select_from(EmailNotification)) == 0
    assert await _product_state(db_session) == before


async def test_pending_resumes_after_expiry_but_an_unused_expired_grant_cannot_start(
    client, db_session, provider
):
    _, _, offer = await _grant(client, db_session)
    first = await _checkout(client, offer)
    offer.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    offer.created_at = offer.expires_at - timedelta(days=7)
    await db_session.commit()
    second = await _checkout(client, offer)
    assert second.json()["id"] == first.json()["id"] and len(provider) == 1
    payment = await db_session.get(Payment, UUID(first.json()["id"]))
    await apply_provider_state(db_session, payment, _state(payment, "canceled"))
    await db_session.commit()
    expired = await _checkout(client, offer)
    assert expired.status_code == 409
    assert expired.json()["error"]["details"]["payment_test_not_created"] is True
    assert (await client.get("/api/billing/purchase-offer")).json() is None


async def test_concurrent_different_keys_share_one_provider_submission(
    client, db_session, test_engine, monkeypatch
):
    from yleum_api.schemas.payments import PaymentTestCheckoutCreate

    user, _, offer = await _grant(client, db_session)
    user_id, offer_id = user.id, offer.id
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    entered, second_reserved, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    original = payments_router.prepare_offer_checkout

    async def prepare(*args, **kwargs):
        result = await original(*args, **kwargs)
        if result[1] is not None:
            second_reserved.set()
        return result

    async def create(**kwargs):
        calls.append(kwargs)
        entered.set()
        await release.wait()
        return {
            "id": "concurrent-test",
            "status": "succeeded",
            "amount": {"value": kwargs["amount"], "currency": "RUB"},
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(payments_router, "prepare_offer_checkout", prepare)
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    monkeypatch.setattr(yookassa, "create_payment", create)

    async def checkout():
        async with factory() as session:
            actor = await session.get(User, user_id)
            return await payments_router.create_test_checkout(
                PaymentTestCheckoutCreate(offer_id=offer_id, idempotency_key=uuid4()),
                actor,
                session,
            )

    first = asyncio.create_task(checkout())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        second = asyncio.create_task(checkout())
        await asyncio.wait_for(second_reserved.wait(), timeout=10)
    finally:
        release.set()
        outcomes = await asyncio.gather(*([first, second] if second else [first]))
    assert len(outcomes) == 2 and outcomes[0].id == outcomes[1].id
    assert len(calls) == 1
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 1


@pytest.mark.parametrize("provider_status", ["succeeded", "canceled"])
async def test_same_key_retry_after_concurrent_settlement_replays_existing_payment(
    client, db_session, test_engine, monkeypatch, provider_status
):
    from yleum_api.schemas.payments import PaymentTestCheckoutCreate

    user, _, offer = await _grant(client, db_session)
    user_id, offer_id, key = user.id, offer.id, uuid4()
    before = await _product_state(db_session)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    retry_entered, release_retry = asyncio.Event(), asyncio.Event()
    original = payments_router.prepare_offer_checkout
    calls = []

    async def prepare(session, **kwargs):
        if session.info.get("delayed_retry"):
            retry_entered.set()
            await release_retry.wait()
        return await original(session, **kwargs)

    async def create(**kwargs):
        calls.append(kwargs)
        return {
            "id": "same-key-test",
            "status": provider_status,
            "amount": {"value": kwargs["amount"], "currency": "RUB"},
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(payments_router, "prepare_offer_checkout", prepare)
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    monkeypatch.setattr(yookassa, "create_payment", create)

    async def checkout(*, delayed=False):
        async with factory() as session:
            session.info["delayed_retry"] = delayed
            actor = await session.get(User, user_id)
            return await payments_router.create_test_checkout(
                PaymentTestCheckoutCreate(offer_id=offer_id, idempotency_key=key), actor, session
            )

    retry = asyncio.create_task(checkout(delayed=True))
    try:
        await asyncio.wait_for(retry_entered.wait(), timeout=10)
        first = await checkout()
    finally:
        release_retry.set()
    replay = await asyncio.wait_for(retry, timeout=10)
    assert replay.id == first.id
    assert replay.status == first.status
    assert len(calls) == 1
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 1
    assert await _product_state(db_session) == before


async def test_lost_webhook_reconciliation_and_mocked_mail_use_test_kind(
    client, db_session, test_engine, provider, monkeypatch
):
    from yleum_api.services import subscription_email

    _, _, offer = await _grant(client, db_session)
    before = await _product_state(db_session)
    created = await _checkout(client, offer)
    payment = await db_session.get(Payment, UUID(created.json()["id"]))
    state = _state(payment)
    await db_session.commit()

    async def get_payment(_provider_id):
        return state

    sent = []

    async def send(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(yookassa, "get_payment", get_payment)
    monkeypatch.setattr(subscription_email, "send_transactional_email", send)
    assert (
        await reconcile_pending_payments(db_session, now=datetime.now(UTC) + timedelta(hours=1))
        == 1
    )
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    assert await subscription_email.deliver_one(factory) is True
    assert await subscription_email.deliver_one(factory) is False
    assert len(sent) == 1
    assert await _product_state(db_session) == before


async def test_a_test_ledger_row_without_operator_grant_cannot_settle(client, db_session, provider):
    user, account, _ = await _grant(client, db_session)
    before = await _product_state(db_session)
    payment = Payment(
        user_id=user.id,
        billing_account_id=account.id,
        idempotency_key=str(uuid4()),
        purpose="payment_test",
        package_code="payment_test",
        amount_rub=Decimal("1.00"),
        credit_rub=Decimal("0"),
        status="pending",
        provider_payment_id="unreserved-test",
    )
    db_session.add(payment)
    await db_session.commit()
    with pytest.raises(ApiError) as rejected:
        await apply_provider_state(db_session, payment, _state(payment))
    assert rejected.value.status_code == 400
    await db_session.rollback()
    await db_session.refresh(payment)
    assert payment.status == "pending"
    assert await db_session.scalar(select(func.count()).select_from(EmailNotification)) == 0
    assert await _product_state(db_session) == before


async def test_only_operator_can_issue_and_reissuance_never_extends_or_replaces_grant(
    client, db_session
):
    from yleum_api.services.billing_offers import issue_billing_purchase_offer

    user, _, offer = await _grant(client, db_session)
    original_expiry = offer.expires_at
    same = await issue_billing_purchase_offer(
        db_session, user_id=user.id, issued_by_user_id=user.id
    )
    assert same.id == offer.id and same.expires_at == original_expiry
    user.role = "user"
    await db_session.flush()
    with pytest.raises(ApiError) as denied:
        await issue_billing_purchase_offer(db_session, user_id=user.id, issued_by_user_id=user.id)
    assert denied.value.status_code == 403
    user.role = "admin"
    await db_session.flush()
    with pytest.raises(ApiError) as expired:
        await issue_billing_purchase_offer(
            db_session,
            user_id=user.id,
            issued_by_user_id=user.id,
            now=original_expiry + timedelta(seconds=1),
        )
    assert expired.value.status_code == 409
