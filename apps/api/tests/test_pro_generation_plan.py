"""Purchased Pro terms cover generation runs, not a prepaid wallet credit."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from tests.account_fixture import register_verified_account
from yleum_api.core.config import get_settings
from yleum_api.models.account import Payment
from yleum_api.models.billing import PRO_PLAN_ID, PRO_PLAN_V2_ID, BillingPlan, Subscription
from yleum_api.routers import payments as payments_router
from yleum_api.services import yookassa


async def test_new_pro_checkout_keeps_wallet_and_grants_five_generations(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    await register_verified_account(client, "included-pro@example.com")
    before = (await client.get("/api/wallet")).json()["balance_rub"]

    async def create(**kwargs):
        return {
            "id": "quota-provider-payment",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/pay"},
        }

    monkeypatch.setattr(yookassa, "create_payment", create)
    response = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "plan_version": 3,
            "idempotency_key": str(uuid4()),
            "auto_renew": False,
            "consent_version": get_settings().legal_document_version,
        },
    )
    assert response.status_code == 201, response.text
    payment = await db_session.get(Payment, UUID(response.json()["id"]))
    assert payment.package_code == "subscription:pro:v3"
    assert payment.amount_rub == Decimal("2990.00")
    assert payment.credit_rub == Decimal("0")

    async def verified_payment(provider_id):
        assert provider_id == "quota-provider-payment"
        return {
            "id": provider_id,
            "status": "succeeded",
            "amount": {"value": "2990.00", "currency": "RUB"},
            "metadata": {"payment_id": str(payment.id)},
        }

    monkeypatch.setattr(yookassa, "get_payment", verified_payment)
    for _ in range(2):
        notified = await client.post(
            "/api/payments/yookassa/webhook",
            json={
                "object": {"id": "quota-provider-payment"},
            },
        )
        assert notified.status_code == 200, notified.text
    subscription = (await client.get("/api/billing/subscription")).json()
    assert subscription["plan"]["entitlements"]["included_generations"] == 5
    assert (await client.get("/api/wallet")).json()["balance_rub"] == before
    usage = (await client.get("/api/billing/usage")).json()["included_generations"]
    assert {k: usage[k] for k in ("limit", "used", "in_progress", "remaining")} == {
        "limit": 5,
        "used": 0,
        "in_progress": 0,
        "remaining": 5,
    }
    assert usage["period_start"] == subscription["current_period_start"]
    assert usage["period_end"] == subscription["current_period_end"]


async def test_old_pro_terms_remain_available_but_new_catalog_has_included_runs(
    client,
    db_session,
):
    plans = (await client.get("/api/billing/plans")).json()
    pro = next(p for p in plans if p["code"] == "pro")
    assert pro["included_credit_rub"] == "0.0000"
    assert pro["entitlements"]["included_generations"] == 5
    legacy = await db_session.scalar(
        select(BillingPlan).where(
            BillingPlan.code == "pro",
            BillingPlan.version == 2,
        )
    )
    assert legacy.included_credit_rub == Decimal("500")
    assert not legacy.is_active


@pytest.mark.parametrize("version", [None, 2])
async def test_old_checkout_terms_cannot_silently_purchase_new_pro(
    client,
    db_session,
    monkeypatch,
    version,
):
    from sqlalchemy import func

    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    await register_verified_account(client, f"stale-terms-{version}@example.com")

    async def create(**kwargs):
        return {
            "id": "must-not-create-stale-order",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/pay"},
        }

    monkeypatch.setattr(yookassa, "create_payment", create)
    response = await client.post(
        "/api/payments/subscription",
        json={
            "plan_code": "pro",
            "plan_version": version,
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "subscription_plan_changed"
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 0


async def test_pending_old_pro_order_replays_its_original_terms(client, db_session, monkeypatch):
    from sqlalchemy import func

    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    await register_verified_account(client, "pending-old-pro@example.com")
    free = await db_session.scalar(select(Subscription))
    pending = Subscription(
        billing_account_id=free.billing_account_id, user_id=free.user_id,
        plan_id=PRO_PLAN_V2_ID, status="pending_payment",
    )
    db_session.add(pending)
    await db_session.flush()
    key = str(uuid4())
    payment = Payment(
        billing_account_id=free.billing_account_id, user_id=free.user_id,
        subscription_id=pending.id, purpose="subscription_initial",
        idempotency_key=key, provider_payment_id="existing-v2-fixture",
        package_code="subscription:pro:v2", amount_rub=Decimal("2990"),
        credit_rub=Decimal("500"), status="pending",
        confirmation_url="https://yookassa.test/existing-order",
    )
    db_session.add(payment)
    await db_session.commit()
    for version in (None, 2, 3):
        response = await client.post("/api/payments/subscription", json={
            "plan_code": "pro", "plan_version": version, "idempotency_key": key,
        })
        assert response.status_code == 201, response.text
        assert response.json()["id"] == str(payment.id)
        assert response.json()["package_code"] == "subscription:pro:v2"
        assert Decimal(response.json()["credit_rub"]) == Decimal("500")
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 1


async def test_included_admission_reserves_five_across_projects_without_wallet(
    client,
    db_session,
    monkeypatch,
):
    from tests.test_free_chat_allowance import no_providers, owner_projects
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.generation_allowance import GenerationAllowance

    owner, projects, account = await owner_projects(db_session, plan_id=PRO_PLAN_ID, count=6)
    subscription = await db_session.scalar(
        select(Subscription).where(
            Subscription.billing_account_id == account.id,
        )
    )
    subscription.current_period_start = datetime.now(UTC) - timedelta(minutes=1)
    subscription.current_period_end = datetime.now(UTC) + timedelta(days=30)
    await db_session.commit()
    jobs = no_providers(monkeypatch, owner)
    # There is no wallet. Even the legacy three-free-build counter must not
    # exempt this account from its purchased five-run allowance.
    assert owner.free_generations_used == 0
    try:
        for i, project in enumerate(projects):
            response = await client.post(
                f"/api/projects/{project.id}/prompt",
                json={
                    "prompt": "Build a fixture task list",
                    "skip_clarify": True,
                    "idempotency_key": f"INCLUDED_{i}",
                },
            )
            assert response.status_code == (202 if i < 5 else 402), response.text
        assert len(jobs) == 5
        assert all(job["is_free"] for job in jobs)
        replay = await client.post(
            f"/api/projects/{projects[0].id}/prompt",
            json={
                "prompt": "Build a fixture task list",
                "skip_clarify": True,
                "idempotency_key": "INCLUDED_0",
            },
        )
        assert replay.status_code == 202 and replay.json()["replayed"]
        assert len(jobs) == 5
        await db_session.rollback()
        reservations = list((await db_session.scalars(select(GenerationAllowance))).all())
        assert len(reservations) == 5
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_pre_dispatch_exception_releases_committed_allowance(db_session, monkeypatch):
    from tests.test_free_chat_allowance import no_providers, owner_projects
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.generation_allowance import GenerationAllowance
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.schemas.message import PromptRequest
    from yleum_api.services.generation.acceptance import PromptAcceptance

    owner, projects, account = await owner_projects(db_session, plan_id=PRO_PLAN_ID)
    subscription = await db_session.scalar(
        select(Subscription).where(
            Subscription.billing_account_id == account.id,
        )
    )
    subscription.current_period_start = datetime.now(UTC) - timedelta(minutes=1)
    subscription.current_period_end = datetime.now(UTC) + timedelta(days=30)
    await db_session.commit()
    jobs = no_providers(monkeypatch, owner)

    async def fail_before_dispatch(self):
        raise RuntimeError("synthetic routing failure")

    monkeypatch.setattr(PromptAcceptance, "select_intent_and_model", fail_before_dispatch)
    try:
        with pytest.raises(RuntimeError, match="synthetic routing failure"):
            await PromptAcceptance(
                project_id=projects[0].id,
                project=projects[0],
                current_user=owner,
                session=db_session,
                payload=PromptRequest(
                    prompt="Build fixture",
                    skip_clarify=True,
                    idempotency_key="ROUTING_FAILURE",
                ),
            ).accept()
        await db_session.rollback()
        run = await db_session.scalar(select(GenerationRun))
        assert run.status == "failed"
        reservation = await db_session.get(GenerationAllowance, run.id)
        assert reservation.status == "released"
        assert not jobs
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize("mode", ["build", "edit"])
async def test_terminal_proof_consumes_included_run_once_without_wallet(db_session, mode):
    from tests.test_free_chat_allowance import owner_projects
    from yleum_api.models.generation_allowance import GenerationAllowance
    from yleum_api.models.generation_billing import GenerationBillingIntent
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.message import Message
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.services.generation_allowance import reserve_included_generation
    from yleum_api.services.generation_billing import admit_policy, seal_terminal_locked
    from yleum_api.services.promotion_permit import (
        MAX_FULL_BUILD_CONTRACT_VERSION,
        MAX_RELEASE_PROOF_CONTRACT_VERSION,
    )

    owner, projects, account = await owner_projects(db_session, plan_id=PRO_PLAN_ID)
    project = projects[0]
    sub = await db_session.scalar(
        select(Subscription).where(
            Subscription.billing_account_id == account.id,
        )
    )
    sub.current_period_start = datetime.now(UTC) - timedelta(days=1)
    sub.current_period_end = datetime.now(UTC) + timedelta(days=29)
    message = Message(project_id=project.id, role="assistant", content="fixture")
    snapshot = Snapshot(project_id=project.id, commit_sha="a" * 40, prompt_text="fixture")
    db_session.add_all([message, snapshot])
    await db_session.flush()
    message.snapshot_id = snapshot.id
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=message.id,
        idempotency_key=str(uuid4()),
        prompt_hash="fixture",
        status="pending",
        response_mode=mode,
    )
    db_session.add(run)
    await db_session.flush()
    assert await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    await admit_policy(db_session, run.id, is_free=True)
    # Synthetic server-side accepted candidate: exercise binding/settlement,
    # without making a paid model call or claiming live runtime verification.
    run.agent_state = {
        "snapshot_id": str(snapshot.id),
        "commit_sha": snapshot.commit_sha,
        "billing_accepted_candidate": {
            "snapshot_id": str(snapshot.id),
            "commit_sha": snapshot.commit_sha,
            "permit_digest": "1" * 64,
            "proof_key": "2" * 64,
            "artifact_digest": "3" * 64,
            "build_contract": MAX_FULL_BUILD_CONTRACT_VERSION,
            "release_contract": MAX_RELEASE_PROOF_CONTRACT_VERSION,
        },
    }
    run.status = "completed"
    await seal_terminal_locked(db_session, run)
    await seal_terminal_locked(db_session, run)
    reservation = await db_session.get(GenerationAllowance, run.id)
    assert reservation.status == "consumed"
    intent = await db_session.get(GenerationBillingIntent, run.id)
    assert intent.outcome == "waived" and intent.amount_rub == 0
