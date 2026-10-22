"""Purchased Pro terms cover generation runs, not a prepaid wallet credit."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from tests.account_fixture import register_verified_account
from yleum_api.core.config import get_settings
from yleum_api.models.account import Payment
from yleum_api.models.billing import (
    BUSINESS_PLAN_ID,
    FREE_PLAN_ID,
    PRO_PLAN_ID,
    PRO_PLAN_V2_ID,
    BillingPlan,
    Subscription,
)
from yleum_api.routers import payments as payments_router
from yleum_api.services import yookassa


@pytest.mark.parametrize(
    "code,price,creations,edits", [("pro", "2990.00", 2, 25), ("business", "6990.00", 5, 50)]
)
async def test_paid_checkout_grants_split_allowances_without_wallet_credit(
    client,
    db_session,
    monkeypatch,
    code,
    price,
    creations,
    edits,
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
            "plan_code": code,
            "plan_version": 3,
            "idempotency_key": str(uuid4()),
            "auto_renew": False,
            "consent_version": get_settings().legal_document_version,
        },
    )
    assert response.status_code == 201, response.text
    payment = await db_session.get(Payment, UUID(response.json()["id"]))
    assert payment.package_code == f"subscription:{code}:v3"
    assert payment.amount_rub == Decimal(price)
    assert payment.credit_rub == Decimal("0")

    async def verified_payment(provider_id):
        assert provider_id == "quota-provider-payment"
        return {
            "id": provider_id,
            "status": "succeeded",
            "amount": {"value": price, "currency": "RUB"},
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
    assert subscription["plan"]["entitlements"]["included_creations"] == creations
    assert subscription["plan"]["entitlements"]["included_edits"] == edits
    assert (await client.get("/api/wallet")).json()["balance_rub"] == before
    usage = (await client.get("/api/billing/usage")).json()["included_generations"]
    for bucket, limit in (("creations", creations), ("edits", edits)):
        assert usage[bucket] == {"limit": limit, "used": 0, "in_progress": 0, "remaining": limit}
    assert usage["period"] == "paid_month"
    assert usage["period_start"] == subscription["current_period_start"]
    assert usage["period_end"] == subscription["current_period_end"]


async def test_old_pro_terms_remain_available_but_new_catalog_has_included_runs(
    client,
    db_session,
):
    plans = (await client.get("/api/billing/plans")).json()
    pro = next(p for p in plans if p["code"] == "pro")
    assert pro["included_credit_rub"] == "0.0000"
    assert pro["entitlements"]["included_creations"] == 2
    assert pro["entitlements"]["included_edits"] == 25
    legacy = await db_session.scalar(
        select(BillingPlan).where(
            BillingPlan.code == "pro",
            BillingPlan.version == 2,
        )
    )
    assert legacy.included_credit_rub == Decimal("500")
    assert not legacy.is_active


@pytest.mark.parametrize("code", ["pro", "business"])
@pytest.mark.parametrize("version", [None, 2])
async def test_old_checkout_terms_cannot_silently_purchase_new_pro(
    client,
    db_session,
    monkeypatch,
    version,
    code,
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
            "plan_code": code,
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
        billing_account_id=free.billing_account_id,
        user_id=free.user_id,
        plan_id=PRO_PLAN_V2_ID,
        status="pending_payment",
    )
    db_session.add(pending)
    await db_session.flush()
    key = str(uuid4())
    payment = Payment(
        billing_account_id=free.billing_account_id,
        user_id=free.user_id,
        subscription_id=pending.id,
        purpose="subscription_initial",
        idempotency_key=key,
        provider_payment_id="existing-v2-fixture",
        package_code="subscription:pro:v2",
        amount_rub=Decimal("2990"),
        credit_rub=Decimal("500"),
        status="pending",
        confirmation_url="https://yookassa.test/existing-order",
    )
    db_session.add(payment)
    await db_session.commit()
    for version in (None, 2, 3):
        response = await client.post(
            "/api/payments/subscription",
            json={
                "plan_code": "pro",
                "plan_version": version,
                "idempotency_key": key,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["id"] == str(payment.id)
        assert response.json()["package_code"] == "subscription:pro:v2"
        assert Decimal(response.json()["credit_rub"]) == Decimal("500")
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 1


@pytest.mark.parametrize("plan_id,limit", [(PRO_PLAN_ID, 2), (BUSINESS_PLAN_ID, 5)])
async def test_creation_admission_reserves_only_its_bucket_without_wallet(
    client,
    db_session,
    monkeypatch,
    plan_id,
    limit,
):
    from tests.test_free_chat_allowance import no_providers, owner_projects
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.generation_allowance import GenerationAllowance

    owner, projects, account = await owner_projects(db_session, plan_id=plan_id, count=limit + 1)
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
    # exempt this account from its purchased creation allowance.
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
            assert response.status_code == (202 if i < limit else 402), response.text
        assert len(jobs) == limit
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
        assert len(jobs) == limit
        await db_session.rollback()
        reservations = list((await db_session.scalars(select(GenerationAllowance))).all())
        assert len(reservations) == limit
        assert {entry.kind for entry in reservations} == {"creation"}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize("plan_id", [FREE_PLAN_ID, PRO_PLAN_ID, BUSINESS_PLAN_ID])
async def test_new_split_plans_cannot_create_unused_wallet_topup_invoices(
    client,
    db_session,
    monkeypatch,
    plan_id,
):
    from unittest.mock import AsyncMock

    from sqlalchemy import func

    await register_verified_account(client, "no-wallet-plan@example.com")
    assert (await client.get("/api/wallet")).json()["balance_rub"] == "0.0000"
    subscription = await db_session.scalar(select(Subscription))
    subscription.plan_id = plan_id
    await db_session.commit()
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    create = AsyncMock(
        return_value={
            "id": "unwanted-wallet-invoice",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/unwanted"},
        }
    )
    monkeypatch.setattr(yookassa, "create_payment", create)
    response = await client.post(
        "/api/payments",
        json={
            "package_code": "start",
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "wallet_topup_unavailable"
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 0
    create.assert_not_awaited()

    from yleum_api.routers import wallet as wallet_router

    settings = get_settings().model_copy(update={"allow_stub_topup": True})
    monkeypatch.setattr(wallet_router, "get_settings", lambda: settings)
    stub = await client.post("/api/wallet/topup", json={"amount_rub": "50"})
    assert stub.status_code == 409, stub.text
    assert stub.json()["error"]["code"] == "wallet_topup_unavailable"
    assert (await client.get("/api/wallet")).json()["balance_rub"] == "0.0000"


async def test_pending_legacy_topup_replays_after_plan_change_and_settles_once(
    client,
    db_session,
    monkeypatch,
):
    from sqlalchemy import func

    from tests.account_fixture import register_legacy_wallet_account
    from yleum_api.models.wallet_charge import WalletCharge

    await register_legacy_wallet_account(client, db_session, "old-topup-owner@example.com")
    subscription = await db_session.scalar(select(Subscription))
    key = str(uuid4())
    old = Payment(
        user_id=subscription.user_id,
        billing_account_id=subscription.billing_account_id,
        purpose="wallet_topup",
        package_code="start",
        amount_rub=Decimal("490"),
        credit_rub=Decimal("500"),
        status="pending",
        idempotency_key=key,
        provider_payment_id="existing-legacy-topup",
        confirmation_url="https://yookassa.test/old-topup",
    )
    db_session.add(old)
    subscription.plan_id = PRO_PLAN_ID
    await db_session.commit()
    monkeypatch.setattr(payments_router, "_configured", lambda: True)
    replay = await client.post(
        "/api/payments", json={"package_code": "start", "idempotency_key": key}
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == str(old.id)

    async def verified(provider_id):
        assert provider_id == old.provider_payment_id
        return {
            "id": provider_id,
            "status": "succeeded",
            "amount": {"value": "490.00", "currency": "RUB"},
            "metadata": {"payment_id": str(old.id)},
        }

    monkeypatch.setattr(yookassa, "get_payment", verified)
    for _ in range(2):
        notification = await client.post(
            "/api/payments/yookassa/webhook",
            json={
                "object": {"id": old.provider_payment_id},
            },
        )
        assert notification.status_code == 200, notification.text
    assert (await client.get("/api/wallet")).json()["balance_rub"] == "600.0000"
    assert await db_session.scalar(select(func.count()).select_from(WalletCharge)) == 1
    await register_verified_account(client, "foreign-topup-owner@example.com")
    conflict = await client.post(
        "/api/payments", json={"package_code": "start", "idempotency_key": key}
    )
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "conflict"
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 1


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
    assert await reserve_included_generation(
        db_session,
        user_id=owner.id,
        run_id=run.id,
        kind="creation" if mode == "build" else "edit",
    )
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


@pytest.mark.parametrize(
    "plan_id,limit", [(FREE_PLAN_ID, 1), (PRO_PLAN_ID, 25), (BUSINESS_PLAN_ID, 50)]
)
@pytest.mark.parametrize("technical_head", [False, True], ids=["generated_head", "config_head"])
async def test_existing_generated_app_uses_edit_bucket_even_for_a_full_rebuild(
    client,
    db_session,
    monkeypatch,
    plan_id,
    limit,
    technical_head,
):
    from tests.test_free_chat_allowance import no_providers, owner_projects
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.generation_allowance import GenerationAllowance
    from yleum_api.models.snapshot import Snapshot

    owner, projects, account = await owner_projects(db_session, plan_id=plan_id, count=2)
    # The old message fence must not prevent the newly granted Free edit.
    owner.free_chat_messages_used = 1
    subscription = await db_session.scalar(
        select(Subscription).where(
            Subscription.billing_account_id == account.id,
        )
    )
    if plan_id != FREE_PLAN_ID:
        subscription.current_period_start = datetime.now(UTC) - timedelta(minutes=1)
        subscription.current_period_end = datetime.now(UTC) + timedelta(days=30)
    for project in projects:
        generated = Snapshot(
            project_id=project.id, commit_sha="b" * 40, prompt_text="Existing generated app"
        )
        db_session.add(generated)
        await db_session.flush()
        project.current_snapshot_id = generated.id
        if technical_head:
            config = Snapshot(
                project_id=project.id, parent_id=generated.id,
                commit_sha="c" * 40, prompt_text=None,
            )
            db_session.add(config)
            await db_session.flush()
            project.current_snapshot_id = config.id
    await db_session.commit()
    jobs = no_providers(monkeypatch, owner)
    try:
        profile = await client.get("/api/auth/me")
        assert profile.status_code == 200, profile.text
        assert profile.json()["user_chat_messages_limit"] is None
        assert profile.json()["user_chat_messages_remaining"] is None
        response = await client.post(
            f"/api/projects/{projects[0].id}/prompt",
            json={
                "prompt": "Completely redesign the existing app",
                "skip_clarify": True,
                "idempotency_key": "SPLIT_EDIT_REBUILD",
            },
        )
        assert response.status_code == 202, response.text
        await db_session.rollback()
        entry = await db_session.get(GenerationAllowance, UUID(response.json()["run_id"]))
        assert entry.kind == "edit" and entry.status == "reserved"
        assert len(jobs) == 1 and jobs[0]["is_free"] is True
        usage = (await client.get("/api/billing/usage")).json()["included_generations"]
        assert usage["edits"] == {
            "limit": limit,
            "used": 0,
            "in_progress": 1,
            "remaining": limit - 1,
        }
        assert usage["creations"]["in_progress"] == 0
        if plan_id == FREE_PLAN_ID:
            denied = await client.post(
                f"/api/projects/{projects[1].id}/prompt",
                json={
                    "prompt": "Edit another existing app",
                    "skip_clarify": True,
                    "idempotency_key": "SPLIT_EDIT_EXHAUSTED",
                },
            )
            assert denied.status_code == 402, denied.text
            assert denied.json()["error"]["details"]["entitlement"] == "included_edits"
            assert denied.json()["error"]["details"]["period"] == "lifetime"
            assert len(jobs) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
