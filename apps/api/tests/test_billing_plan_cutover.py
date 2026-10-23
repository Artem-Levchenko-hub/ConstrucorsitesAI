"""Close the genuine old-commerce-worker downgrade window before release unlock."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.test_free_chat_allowance import owner_projects
from yleum_api.models.billing import FREE_PLAN_ID, FREE_PLAN_V3_ID, PRO_PLAN_V2_ID, Subscription
from yleum_api.models.wallet import Wallet
from yleum_api.services import subscription_lifecycle
from yleum_api.services.billing_plan_cutover import normalize_live_free_subscriptions


async def test_old_billing_worker_downgrade_is_normalized_once_without_touching_money(
    db_session,
    monkeypatch,
):
    from decimal import Decimal

    owner, _, account = await owner_projects(db_session, plan_id=PRO_PLAN_V2_ID)
    old = await db_session.scalar(select(Subscription).where(Subscription.user_id == owner.id))
    db_session.add(
        Wallet(user_id=owner.id, billing_account_id=account.id, balance_rub=Decimal("275.25"))
    )
    owner.free_creations_carryover = 1
    # Emulate the existing old worker's imported constant after the new schema
    # is already installed, before its separately verified rollout retires it.
    monkeypatch.setattr(subscription_lifecycle, "FREE_PLAN_ID", FREE_PLAN_V3_ID)
    monkeypatch.setattr(subscription_lifecycle, "_notify", AsyncMock())
    await subscription_lifecycle.downgrade_to_free(db_session, old, now=datetime.now(UTC))
    await db_session.commit()
    active = await db_session.scalar(
        select(Subscription).where(
            Subscription.user_id == owner.id,
            Subscription.status == "active",
        )
    )
    assert active.plan_id == FREE_PLAN_V3_ID
    assert await normalize_live_free_subscriptions(db_session) == 1
    await db_session.commit()
    assert await normalize_live_free_subscriptions(db_session) == 0
    await db_session.refresh(active)
    await db_session.refresh(old)
    await db_session.refresh(owner)
    assert active.plan_id == FREE_PLAN_ID
    assert old.status == "expired" and old.plan_id == PRO_PLAN_V2_ID
    assert owner.free_creations_carryover == 1
    wallet = await db_session.scalar(select(Wallet).where(Wallet.billing_account_id == account.id))
    assert wallet.balance_rub == Decimal("275.25")


async def test_normalization_preserves_active_paid_and_ended_free_history(db_session):
    owner, _, _ = await owner_projects(db_session, plan_id=PRO_PLAN_V2_ID)
    paid = await db_session.scalar(select(Subscription).where(Subscription.user_id == owner.id))
    ended = Subscription(
        user_id=owner.id,
        billing_account_id=paid.billing_account_id,
        plan_id=FREE_PLAN_V3_ID,
        status="expired",
    )
    db_session.add(ended)
    await db_session.commit()
    assert await normalize_live_free_subscriptions(db_session) == 0
    await db_session.refresh(paid)
    await db_session.refresh(ended)
    assert paid.plan_id == PRO_PLAN_V2_ID and paid.status == "active"
    assert ended.plan_id == FREE_PLAN_V3_ID and ended.status == "expired"


async def test_operator_cutover_requires_matching_runtime_and_owned_drain(db_session, monkeypatch):
    from types import SimpleNamespace

    from yleum_api.core import db
    from yleum_api.services import billing_plan_cutover
    from yleum_api.services.generation_deployment_drain import begin_drain, drain_status

    owner, _, _ = await owner_projects(db_session, plan_id=FREE_PLAN_V3_ID)
    active = await db_session.scalar(select(Subscription).where(Subscription.user_id == owner.id))
    await db_session.commit()
    expected = "a" * 40
    monkeypatch.setattr(
        billing_plan_cutover, "get_settings", lambda: SimpleNamespace(omnia_release_sha=expected)
    )
    monkeypatch.setattr(db, "get_engine", lambda: db_session.bind)
    monkeypatch.setattr(db, "dispose_engine", AsyncMock())
    with pytest.raises(ValueError, match="target release"):
        await billing_plan_cutover._command("b" * 40)
    with pytest.raises(ValueError, match="owned deployment drain"):
        await billing_plan_cutover._command(expected)
    await db_session.refresh(active)
    assert active.plan_id == FREE_PLAN_V3_ID
    await begin_drain(db_session, expected)
    await db_session.commit()
    await billing_plan_cutover._command(expected)
    await db_session.refresh(active)
    assert active.plan_id == FREE_PLAN_ID
    assert (await drain_status(db_session))["release_sha"] == expected
