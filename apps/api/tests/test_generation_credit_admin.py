"""Administrative exhaustion changes rights, never their immutable financial sources."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_credit_units import owner, payment, run_for
from yleum_api.core.errors import ApiError
from yleum_api.models.account import Payment
from yleum_api.models.admin_audit import AdminAuditEvent
from yleum_api.models.billing import PRO_PLAN_V3_ID, BillingPlan, Subscription
from yleum_api.models.generation_credits import (
    GenerationCreditAdjustment,
    GenerationCreditGrant,
    GenerationCreditReservation,
)
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.user import User
from yleum_api.models.wallet import Wallet
from yleum_api.services import generation_credit_admin as admin
from yleum_api.services import generation_credits as credits


async def funded_owner(session):
    user, account = await owner(session)
    user.is_anon = False
    user.role = "admin"
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    sub.plan_id = PRO_PLAN_V3_ID
    sub.current_period_start = datetime.now(UTC) - timedelta(minutes=1)
    sub.current_period_end = sub.current_period_start + timedelta(days=30)
    plan = await session.get(BillingPlan, PRO_PLAN_V3_ID)
    value = Payment(
        billing_account_id=account.id,
        user_id=user.id,
        idempotency_key=uuid4().hex,
        package_code="subscription:pro:v3",
        purpose="subscription_initial",
        subscription_id=sub.id,
        amount_rub=Decimal(1),
        credit_rub=Decimal(0),
        status="succeeded",
    )
    session.add(value)
    await session.flush()
    await credits.grant_subscription_units(session, sub, value, plan)
    await payment(session, user, account, units=5, edits_only=True)
    await payment(session, user, account, units=15)
    session.add(Wallet(user_id=user.id, billing_account_id=account.id, balance_rub=Decimal(42)))
    await session.flush()
    return user, account, sub, plan


async def exhaust(session, user, operation_id=None, **kwargs):
    return await admin.exhaust_available_credits(
        session,
        target_user_id=user.id,
        actor_user_id=user.id,
        operation_id=operation_id or uuid4(),
        reason="Exercise exhausted plan",
        **kwargs,
    )


async def protected_rows(session):
    result = {}
    for model in (
        Payment,
        Subscription,
        Wallet,
        GenerationCreditGrant,
        GenerationCreditReservation,
    ):
        rows = (await session.execute(select(model.__table__))).all()
        result[model.__tablename__] = sorted((tuple(row) for row in rows), key=str)
    return result


async def test_exhaust_all_buckets_preserves_sources_and_accepted_facts(db_session):
    user, account, _sub, _plan = await funded_owner(db_session)
    run, project = await run_for(db_session, user)
    await credits.reserve_generation_units(db_session, run)
    run.status = "completed"
    run.response_mode = "build"
    run.agent_state = {"billing_accepted_candidate": "accepted"}
    await credits.settle_generation_units(db_session, run, accepted=True, snapshot_id=uuid4())
    before = await protected_rows(db_session)
    result = await exhaust(db_session, user)
    assert result["debited_by_bucket"] == {"initial": 1, "edit": 25, "edit_only": 5, "flexible": 15}
    assert result["replayed"] is False
    grants, _, _ = await credits._inventory(db_session, account, user.id)
    assert all(grant["available"] == 0 for grant in grants)
    assert await protected_rows(db_session) == before
    await db_session.refresh(run)
    await db_session.refresh(project)
    assert run.status == "completed" and run.agent_state == {
        "billing_accepted_candidate": "accepted"
    }
    assert project.generation_initial_accepted is True
    audit = await db_session.scalar(select(AdminAuditEvent))
    assert audit.action == "generation_credits.exhaust"
    assert audit.details["before"] == result["debited_by_bucket"]
    assert all(value == 0 for value in audit.details["after"].values())


async def test_replay_keeps_new_purchase_and_other_account_untouched(db_session):
    user, account, _sub, _plan = await funded_owner(db_session)
    other, other_account, _, _ = await funded_owner(db_session)
    other_before = await credits.credits_view(db_session, other.id)
    operation = uuid4()
    first = await exhaust(db_session, user, operation)
    await payment(db_session, user, account, units=9)
    replay = await exhaust(db_session, user, operation)
    assert replay == {**first, "replayed": True}
    assert (await credits.credits_view(db_session, user.id))["flexible_units"] == 9
    assert await credits.credits_view(db_session, other.id) == other_before
    assert len((await db_session.scalars(select(GenerationCreditAdjustment))).all()) == 1
    assert len((await db_session.scalars(select(AdminAuditEvent))).all()) == 1
    assert other_account.id != account.id
    with pytest.raises(ApiError) as failure:
        await exhaust(db_session, other, operation)
    assert failure.value.status_code == 409


async def test_new_paid_period_unaffected(db_session):
    user, _account, sub, plan = await funded_owner(db_session)
    await exhaust(db_session, user)
    sub.current_period_start += timedelta(seconds=1)
    sub.current_period_end += timedelta(seconds=1)
    renewal = Payment(
        billing_account_id=sub.billing_account_id,
        user_id=user.id,
        idempotency_key=uuid4().hex,
        package_code="subscription:pro:v3",
        purpose="subscription_renewal",
        subscription_id=sub.id,
        amount_rub=Decimal(1),
        credit_rub=Decimal(0),
        status="succeeded",
    )
    db_session.add(renewal)
    await db_session.flush()
    await credits.grant_subscription_units(db_session, sub, renewal, plan)
    view = await credits.credits_view(db_session, user.id)
    assert view["included_generations_remaining"] == 2
    assert view["included_edits_remaining"] == 25


@pytest.mark.parametrize("held", [False, True])
async def test_active_run_or_held_reservation_blocks_without_mutation(db_session, held):
    user, _account, _, _ = await funded_owner(db_session)
    run, _ = await run_for(db_session, user)
    if held:
        await credits.reserve_generation_units(db_session, run)
        run.status = "completed"  # An unresolved hold still blocks after a terminal run.
    before = await protected_rows(db_session)
    with pytest.raises(ApiError) as failure:
        await exhaust(db_session, user)
    assert failure.value.status_code == 409
    assert await protected_rows(db_session) == before
    assert (await db_session.scalar(select(GenerationCreditAdjustment.id))) is None
    assert (await db_session.scalar(select(AdminAuditEvent.id))) is None


async def test_concurrent_reservation_commits_before_exhaust_checks_inventory(
    db_session, test_engine
):
    user, _account, _, _ = await funded_owner(db_session)
    run, _ = await run_for(db_session, user)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as reservation_session, factory() as admin_session:
        await credits.reserve_generation_units(reservation_session, run)
        pending = asyncio.create_task(exhaust(admin_session, user))
        await asyncio.sleep(0.05)
        assert not pending.done()
        await reservation_session.commit()
        with pytest.raises(ApiError) as failure:
            await asyncio.wait_for(pending, timeout=5)
        assert failure.value.status_code == 409
        assert (await admin_session.scalar(select(GenerationCreditAdjustment.id))) is None


@pytest.mark.parametrize("invalid", ["nonadmin", "inactive", "anon", "unlimited", "qa"])
async def test_invalid_actor_target_or_billing_rejected(db_session, monkeypatch, invalid):
    from yleum_api.core.config import get_settings

    user, _account, sub, _ = await funded_owner(db_session)
    if invalid == "nonadmin":
        user.role = "user"
    elif invalid == "inactive":
        user.status = "blocked"
    elif invalid == "anon":
        user.is_anon = True
    elif invalid == "unlimited":
        monkeypatch.setattr(get_settings(), "unlimited_generations", True)
    else:
        qa = BillingPlan(
            id=uuid4(),
            code="qa_internal",
            version=99,
            name="Private QA",
            price_rub=Decimal(0),
            is_active=False,
            entitlements={"qa_unlimited_generations": True},
        )
        db_session.add(qa)
        await db_session.flush()
        sub.plan_id = qa.id
    await db_session.flush()
    with pytest.raises(ApiError):
        await exhaust(db_session, user)
    assert (await db_session.scalar(select(GenerationCreditAdjustment.id))) is None


async def test_empty_exhaust_is_durable_and_does_not_consume_future_purchase(db_session):
    user, account, _, _ = await funded_owner(db_session)
    await exhaust(db_session, user)
    operation = uuid4()
    empty = await exhaust(db_session, user, operation)
    assert all(value == 0 for value in empty["debited_by_bucket"].values())
    await payment(db_session, user, account, units=7)
    assert (await exhaust(db_session, user, operation))["replayed"] is True
    assert (await credits.credits_view(db_session, user.id))["flexible_units"] == 7


async def test_reversal_restores_only_original_rights_and_replay_never_debits(db_session):
    user, account, _, _ = await funded_owner(db_session)
    before = await credits.credits_view(db_session, user.id)
    debit_operation, reversal_operation = uuid4(), uuid4()
    await exhaust(db_session, user, debit_operation)
    kwargs = dict(
        adjustment_id=debit_operation,
        actor_user_id=user.id,
        operation_id=reversal_operation,
        reason="Restore test allowance",
    )
    restored = await admin.restore_exhausted_credits(db_session, **kwargs)
    assert restored["replayed"] is False
    assert await credits.credits_view(db_session, user.id) == before
    await payment(db_session, user, account, units=7)
    assert (await admin.restore_exhausted_credits(db_session, **kwargs))["replayed"] is True
    assert (await exhaust(db_session, user, debit_operation))["replayed"] is True
    assert (await credits.credits_view(db_session, user.id))["flexible_units"] == 22
    with pytest.raises(ApiError) as failure:
        await admin.restore_exhausted_credits(db_session, **{**kwargs, "operation_id": uuid4()})
    assert failure.value.status_code == 409


@pytest.mark.parametrize("units", [-1, 0, True, "1"])
def test_malformed_debit_allocations_fail_closed(units):
    grant_id = uuid4()
    grant = GenerationCreditGrant(id=grant_id, bucket="edit", units=25)
    with pytest.raises(RuntimeError):
        credits._allocation_usage(
            [{"grant_id": str(grant_id), "bucket": "edit", "units": units}],
            {str(grant_id): grant},
            require_bucket=True,
        )


@pytest.mark.parametrize("invalid", ["foreign_grant", "wrong_bucket", "unknown_bucket", "bad_uuid"])
def test_invalid_allocation_binding_fails_closed(invalid):
    grant_id = uuid4()
    grant = GenerationCreditGrant(id=grant_id, bucket="edit", units=25)
    allocation = {"grant_id": str(grant_id), "bucket": "edit", "units": 1}
    if invalid == "foreign_grant":
        allocation["grant_id"] = str(uuid4())
    elif invalid == "wrong_bucket":
        allocation["bucket"] = "initial"
    elif invalid == "unknown_bucket":
        allocation["bucket"] = "money"
    else:
        allocation["grant_id"] = "invalid"
    with pytest.raises(RuntimeError):
        credits._allocation_usage([allocation], {str(grant_id): grant}, require_bucket=True)


async def test_concurrent_same_operation_is_one_debit_and_one_audit(db_session, test_engine):
    user, _, _, _ = await funded_owner(db_session)
    await db_session.commit()
    operation = uuid4()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def attempt():
        async with factory() as session:
            result = await exhaust(session, user, operation)
            await session.commit()
            return result

    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), timeout=10)
    assert sorted(row["replayed"] for row in results) == [False, True]
    assert results[0]["debited_by_bucket"] == results[1]["debited_by_bucket"]
    assert len((await db_session.scalars(select(GenerationCreditAdjustment))).all()) == 1
    assert len((await db_session.scalars(select(AdminAuditEvent))).all()) == 1


async def test_reversal_never_restores_expired_period(db_session, monkeypatch):
    user, _, sub, _ = await funded_owner(db_session)
    operation = uuid4()
    await exhaust(db_session, user, operation)
    future = sub.current_period_end + timedelta(seconds=1)

    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(credits, "datetime", Future)
    await admin.restore_exhausted_credits(
        db_session,
        adjustment_id=operation,
        actor_user_id=user.id,
        operation_id=uuid4(),
        reason="Restore original rights",
    )
    view = await credits.credits_view(db_session, user.id)
    assert view["included_generations_remaining"] == 0
    assert view["included_edits_remaining"] == 0
    assert view["edit_only_units"] == 5 and view["flexible_units"] == 15


@pytest.mark.parametrize("transition", ["renewal", "released_hold", "succeeded_payment"])
async def test_exhaust_refreshes_preloaded_state_after_account_lock(
    db_session,
    test_engine,
    monkeypatch,
    transition,
):
    user, account, sub, plan = await funded_owner(db_session)
    run_id = None
    if transition == "released_hold":
        run, _ = await run_for(db_session, user)
        run_id = run.id
        await credits.reserve_generation_units(db_session, run)
    pending_payment = Payment(
        billing_account_id=account.id,
        user_id=user.id,
        idempotency_key=uuid4().hex,
        package_code="synthetic",
        amount_rub=Decimal(1),
        credit_rub=Decimal(0),
        status="pending",
        edit_units=9,
        edits_only=False,
        package_version=2,
    )
    db_session.add(pending_payment)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as stale, factory() as writer:
        # Retain strong references: SQLAlchemy's identity map otherwise releases them.
        cached_sub = await stale.get(Subscription, sub.id)
        cached_plan = await stale.get(BillingPlan, plan.id)
        cached_payment = await stale.get(Payment, pending_payment.id)
        cached_hold = await stale.get(GenerationCreditReservation, run_id) if run_id else None
        await credits._lock_account(writer, user.id)
        if transition == "renewal":
            renewed_sub = await writer.get(Subscription, sub.id)
            renewed_sub.current_period_start += timedelta(seconds=1)
            renewed_sub.current_period_end += timedelta(seconds=1)
            renewal = Payment(
                billing_account_id=account.id,
                user_id=user.id,
                idempotency_key=uuid4().hex,
                package_code="subscription:pro:v3",
                purpose="subscription_renewal",
                subscription_id=sub.id,
                amount_rub=Decimal(1),
                credit_rub=Decimal(0),
                status="succeeded",
            )
            writer.add(renewal)
            await writer.flush()
            await credits.grant_subscription_units(writer, renewed_sub, renewal, cached_plan)
        elif transition == "released_hold":
            released_run = await writer.get(GenerationRun, run_id)
            released_run.status = "failed"
            await credits.settle_generation_units(
                writer, released_run, accepted=False, snapshot_id=None
            )
        else:
            succeeded_payment = await writer.get(Payment, pending_payment.id)
            succeeded_payment.status = "succeeded"
            await credits.grant_payment_units(writer, succeeded_payment)
        await writer.flush()
        before = await protected_rows(writer)
        lock_attempted = asyncio.Event()
        real_lock = admin._lock_account

        async def waiting_lock(session, target_id):
            lock_attempted.set()
            return await real_lock(session, target_id)

        monkeypatch.setattr(admin, "_lock_account", waiting_lock)
        task = asyncio.create_task(exhaust(stale, user))
        await asyncio.wait_for(lock_attempted.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not task.done()
        assert cached_sub.current_period_start == sub.current_period_start
        assert cached_payment.status == "pending"
        if cached_hold:
            assert cached_hold.state == "held"
        await writer.commit()
        result = await asyncio.wait_for(task, timeout=5)
        await stale.commit()
        assert result["debited_by_bucket"] == {
            "initial": 2,
            "edit": 25,
            "edit_only": 5,
            "flexible": 24 if transition == "succeeded_payment" else 15,
        }
        if transition == "renewal":
            assert cached_sub.current_period_start == renewed_sub.current_period_start
        elif transition == "released_hold":
            assert cached_hold.state == "released"
        else:
            assert cached_payment.status == "succeeded"
    async with factory() as fresh:
        view = await credits.credits_view(fresh, user.id)
        assert view["included_generations_remaining"] == 0
        assert view["included_edits_remaining"] == 0
        assert view["edit_only_units"] == 0 and view["flexible_units"] == 0
        assert await protected_rows(fresh) == before


async def test_exhaust_reauthorizes_after_waiting_for_account_lock(
    db_session,
    test_engine,
    monkeypatch,
):
    user, _, _, _ = await funded_owner(db_session)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as stale, factory() as writer:
        cached_user = await stale.get(User, user.id)
        await credits._lock_account(writer, user.id)
        writer_user = await writer.get(User, user.id)
        writer_user.role = "user"
        await writer.flush()
        lock_attempted = asyncio.Event()
        real_lock = admin._lock_account

        async def waiting_lock(session, target_id):
            lock_attempted.set()
            return await real_lock(session, target_id)

        monkeypatch.setattr(admin, "_lock_account", waiting_lock)
        task = asyncio.create_task(exhaust(stale, user))
        await asyncio.wait_for(lock_attempted.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not task.done() and cached_user.role == "admin"
        await writer.commit()
        with pytest.raises(ApiError) as failure:
            await asyncio.wait_for(task, timeout=5)
        assert failure.value.status_code == 403
        assert await stale.scalar(select(GenerationCreditAdjustment.id)) is None


async def test_restore_refreshes_released_hold_before_audit(db_session, test_engine, monkeypatch):
    user, account, _, _ = await funded_owner(db_session)
    debit_operation = uuid4()
    await exhaust(db_session, user, debit_operation)
    await payment(db_session, user, account, units=15)
    run, _ = await run_for(db_session, user)
    await credits.reserve_generation_units(db_session, run)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as stale, factory() as writer:
        cached_hold = await stale.get(GenerationCreditReservation, run.id)
        writer_run = await writer.get(GenerationRun, run.id)
        writer_run.status = "failed"
        await credits.settle_generation_units(writer, writer_run, accepted=False, snapshot_id=None)
        lock_attempted = asyncio.Event()
        real_lock = admin._lock_account

        async def waiting_lock(session, target_id):
            lock_attempted.set()
            return await real_lock(session, target_id)

        monkeypatch.setattr(admin, "_lock_account", waiting_lock)
        task = asyncio.create_task(
            admin.restore_exhausted_credits(
                stale,
                adjustment_id=debit_operation,
                actor_user_id=user.id,
                operation_id=uuid4(),
                reason="Restore after release",
            )
        )
        await asyncio.wait_for(lock_attempted.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not task.done() and cached_hold.state == "held"
        await writer.commit()
        await asyncio.wait_for(task, timeout=5)
        await stale.commit()
        assert cached_hold.state == "released"
    async with factory() as fresh:
        audit = await fresh.scalar(
            select(AdminAuditEvent).where(
                AdminAuditEvent.action == "generation_credits.restore",
            )
        )
        assert audit.details["before"]["flexible"] == 15
        assert audit.details["after"]["flexible"] == 30
        assert (await credits.credits_view(fresh, user.id))["flexible_units"] == 30
