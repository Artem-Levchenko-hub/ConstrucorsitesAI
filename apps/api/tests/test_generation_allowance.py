from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.generation_allowance import GenerationAllowance
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.services.entitlements import load_plan_context
from yleum_api.services.generation_allowance import (
    quota_usage,
    reserve_included_generation,
    settle_included_generation,
)

pytestmark = pytest.mark.asyncio
Kind = Literal["creation", "edit"]
WORKER_GRANTS = (
    Path(__file__).resolve().parents[3]
    / "infra/max-k3s/commerce/remote/billing-generation-grants.sql"
)


async def _account(
    session: AsyncSession,
    code: str = "pro",
) -> tuple[User, Subscription]:
    owner = User(email=f"allowance-{uuid4().hex}@example.com")
    session.add(owner)
    await session.flush()
    account = BillingAccount(personal_user_id=owner.id, created_by_user_id=owner.id)
    session.add(account)
    plan = await session.scalar(
        select(BillingPlan).where(BillingPlan.code == code, BillingPlan.is_active.is_(True))
    )
    assert plan is not None
    limits = {"free": (1, 1), "pro": (2, 25), "business": (5, 50)}[code]
    plan.entitlements = {
        **{key: value for key, value in plan.entitlements.items() if key != "included_generations"},
        "included_creations": limits[0],
        "included_edits": limits[1],
        "generation_allowance_period": "lifetime" if code == "free" else "paid_month",
    }
    await session.flush()
    now = datetime.now(UTC)
    subscription = Subscription(
        billing_account_id=account.id,
        user_id=owner.id,
        plan_id=plan.id,
        status="active",
        current_period_start=None if code == "free" else now - timedelta(days=1),
        current_period_end=None if code == "free" else now + timedelta(days=29),
    )
    session.add(subscription)
    await session.flush()
    return owner, subscription


async def _run(session: AsyncSession, owner: User) -> GenerationRun:
    project = Project(
        owner_id=owner.id,
        name="Allowance test",
        slug=f"allowance-{uuid4().hex}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=uuid4().hex,
        prompt_hash="a" * 64,
    )
    session.add(run)
    await session.flush()
    return run


async def _reserve(
    session: AsyncSession,
    owner: User,
    kind: Kind,
) -> GenerationRun:
    run = await _run(session, owner)
    assert await reserve_included_generation(session, user_id=owner.id, run_id=run.id, kind=kind)
    return run


async def _consume(session: AsyncSession, run: GenerationRun, mode: str = "build") -> None:
    run.status, run.response_mode = "completed", mode
    await settle_included_generation(session, run, accepted=True)


@pytest.mark.parametrize(
    ("code", "creation_limit", "edit_limit"),
    [("free", 1, 1), ("pro", 2, 25), ("business", 5, 50)],
)
async def test_creation_and_edit_buckets_enforce_independent_limits(
    db_session: AsyncSession,
    code: str,
    creation_limit: int,
    edit_limit: int,
) -> None:
    owner, subscription = await _account(db_session, code)
    for _ in range(creation_limit):
        await _consume(db_session, await _reserve(db_session, owner, "creation"))
    rejected_creation = await _run(db_session, owner)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(
            db_session, user_id=owner.id, run_id=rejected_creation.id, kind="creation"
        )
    assert refused.value.status_code == 402
    assert refused.value.details["entitlement"] == "included_creations"
    period = "lifetime" if code == "free" else "paid_month"
    assert refused.value.details["period"] == period
    for _ in range(edit_limit):
        # A major edit may return build; the persisted admission kind owns its bucket.
        await _consume(db_session, await _reserve(db_session, owner, "edit"), mode="build")
    rejected_edit = await _run(db_session, owner)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(
            db_session, user_id=owner.id, run_id=rejected_edit.id, kind="edit"
        )
    assert refused.value.status_code == 402
    assert refused.value.details["entitlement"] == "included_edits"
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert usage == {
        "period": period,
        "period_start": subscription.current_period_start,
        "period_end": subscription.current_period_end,
        "creations": {
            "limit": creation_limit,
            "used": creation_limit,
            "in_progress": 0,
            "remaining": 0,
        },
        "edits": {"limit": edit_limit, "used": edit_limit, "in_progress": 0, "remaining": 0},
    }


@pytest.mark.parametrize("kind", ["creation", "edit"])
@pytest.mark.parametrize("status", ["failed", "cancelled", "completed"])
async def test_unsuccessful_free_run_releases_its_bucket_for_a_new_retry(
    db_session: AsyncSession,
    kind: Kind,
    status: str,
) -> None:
    owner, _ = await _account(db_session, "free")
    run = await _reserve(db_session, owner, kind)
    run.status, run.response_mode = status, "advice" if status == "completed" else "build"
    await settle_included_generation(db_session, run, accepted=False)
    await settle_included_generation(db_session, run, accepted=True)
    entry = await db_session.get(GenerationAllowance, run.id)
    assert (entry.kind, entry.status) == (kind, "released")
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id, kind=kind)
    assert refused.value.status_code == 409
    retry = await _reserve(db_session, owner, kind)
    await _consume(db_session, retry)
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert usage["creations" if kind == "creation" else "edits"]["used"] == 1


async def test_frozen_free_carryover_spends_only_creation_and_ignores_live_legacy_counter(
    db_session: AsyncSession,
) -> None:
    owner, _ = await _account(db_session, "free")
    owner.free_creations_carryover = 1
    owner.free_generations_used = 99
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert usage["creations"] == {"limit": 1, "used": 1, "in_progress": 0, "remaining": 0}
    assert usage["edits"] == {"limit": 1, "used": 0, "in_progress": 0, "remaining": 1}
    creation = await _run(db_session, owner)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(
            db_session, user_id=owner.id, run_id=creation.id, kind="creation"
        )
    assert refused.value.details["entitlement"] == "included_creations"
    await _consume(db_session, await _reserve(db_session, owner, "edit"))
    assert len((await db_session.scalars(select(GenerationAllowance))).all()) == 1


async def test_unfrozen_paid_legacy_counter_does_not_spend_free_creation(
    db_session: AsyncSession,
) -> None:
    owner, _ = await _account(db_session, "free")
    owner.free_generations_used = 12
    assert owner.free_creations_carryover == 0
    await _consume(db_session, await _reserve(db_session, owner, "creation"))
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert usage["creations"]["used"] == 1
    assert usage["edits"]["remaining"] == 1


async def _switch_plan(
    session: AsyncSession,
    subscription: Subscription,
    code: str,
) -> Subscription:
    subscription.status = "canceled"
    await session.flush()
    plan = await session.scalar(
        select(BillingPlan).where(BillingPlan.code == code, BillingPlan.is_active.is_(True))
    )
    now = datetime.now(UTC)
    replacement = Subscription(
        user_id=subscription.user_id,
        billing_account_id=subscription.billing_account_id,
        plan_id=plan.id,
        status="active",
        current_period_start=None if code == "free" else now - timedelta(hours=1),
        current_period_end=None if code == "free" else now + timedelta(days=30),
    )
    session.add(replacement)
    await session.flush()
    return replacement


async def test_lifetime_usage_survives_deletion_paid_upgrade_and_free_subscription_replacement(
    db_session: AsyncSession,
) -> None:
    owner, free = await _account(db_session, "free")
    original = await _reserve(db_session, owner, "creation")
    await _consume(db_session, original)
    original_id = original.id
    await db_session.execute(delete(Project).where(Project.id == original.project_id))
    await db_session.commit()
    assert (
        await db_session.scalar(select(GenerationRun.id).where(GenerationRun.id == original_id))
        is None
    )
    assert (await db_session.get(GenerationAllowance, original_id)).status == "consumed"
    paid = await _switch_plan(db_session, free, "pro")
    await _consume(db_session, await _reserve(db_session, owner, "creation"))
    await _consume(db_session, await _reserve(db_session, owner, "edit"))
    owner.free_generations_used = 20  # Paid included completions may update this old counter.
    back_to_free = await _switch_plan(db_session, paid, "free")
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert usage["creations"]["used"] == 1
    assert usage["edits"]["remaining"] == 1
    await _consume(db_session, await _reserve(db_session, owner, "edit"))
    await _switch_plan(db_session, back_to_free, "free")
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert usage["period"] == "lifetime"
    assert (usage["period_start"], usage["period_end"]) == (None, None)
    assert usage["creations"]["remaining"] == usage["edits"]["remaining"] == 0


@pytest.mark.parametrize("code", ["free", "pro"])
async def test_concurrent_admission_serializes_both_buckets_without_fk_deadlock(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    code: str,
) -> None:
    owner, subscription = await _account(db_session, code)
    if code == "pro":
        for _ in range(23):
            await _consume(db_session, await _reserve(db_session, owner, "edit"))
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ready = asyncio.Barrier(8)

    async def admit(kind: Kind) -> tuple[Kind, bool]:
        async with factory() as session:
            run = await _run(session, owner)
            table, key = (User, owner.id) if code == "free" else (Subscription, subscription.id)
            await session.execute(
                select(table.id).where(table.id == key).with_for_update(read=True, key_share=True)
            )
            await ready.wait()
            try:
                result = await reserve_included_generation(
                    session, user_id=owner.id, run_id=run.id, kind=kind
                )
                await session.commit()
                return kind, result
            except ApiError as error:
                assert error.status_code == 402
                return kind, False

    results = await asyncio.wait_for(
        asyncio.gather(*(admit(kind) for kind in ("creation", "edit") for _ in range(4))),
        timeout=20,
    )
    expected = 1 if code == "free" else 2
    assert results.count(("creation", True)) == results.count(("edit", True)) == expected


async def test_concurrent_same_run_replay_reserves_one_immutable_kind(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner, _ = await _account(db_session, "free")
    run = await _run(db_session, owner)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ready = asyncio.Barrier(4)

    async def replay() -> bool:
        async with factory() as session:
            await ready.wait()
            included = await reserve_included_generation(
                session, user_id=owner.id, run_id=run.id, kind="edit"
            )
            await session.commit()
            return included

    assert await asyncio.wait_for(asyncio.gather(*(replay() for _ in range(4))), timeout=15) == [
        True,
        True,
        True,
        True,
    ]
    entries = (await db_session.scalars(select(GenerationAllowance))).all()
    assert len(entries) == 1
    assert entries[0].kind == "edit"
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(
            db_session, user_id=owner.id, run_id=run.id, kind="creation"
        )
    assert refused.value.code == "idempotency_conflict"


async def test_replay_checks_user_and_account_binding(db_session: AsyncSession) -> None:
    owner, _ = await _account(db_session)
    run = await _reserve(db_session, owner, "creation")
    other, other_subscription = await _account(db_session)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(
            db_session, user_id=other.id, run_id=run.id, kind="creation"
        )
    assert refused.value.status_code == 409
    entry = await db_session.get(GenerationAllowance, run.id)
    entry.billing_account_id = other_subscription.billing_account_id
    await db_session.flush()
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(
            db_session, user_id=owner.id, run_id=run.id, kind="creation"
        )
    assert refused.value.status_code == 409


async def test_settlement_and_replay_refresh_stale_ledger_state(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner, _ = await _account(db_session)
    consumed = await _reserve(db_session, owner, "creation")
    released = await _reserve(db_session, owner, "edit")
    consumed.status, consumed.response_mode = "completed", "build"
    released.status = "failed"
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as stale, factory() as terminal:
        cached_consumed = await stale.get(GenerationAllowance, consumed.id)
        cached_released = await stale.get(GenerationAllowance, released.id)
        assert cached_consumed.status == cached_released.status == "reserved"
        for run_id, accepted in ((consumed.id, True), (released.id, False)):
            run = await terminal.get(GenerationRun, run_id, with_for_update=True)
            await settle_included_generation(terminal, run, accepted=accepted)
            await terminal.commit()
        run = await stale.get(GenerationRun, consumed.id, with_for_update=True)
        await settle_included_generation(stale, run, accepted=False)
        await stale.commit()
        with pytest.raises(ApiError) as refused:
            await reserve_included_generation(
                stale, user_id=owner.id, run_id=released.id, kind="edit"
            )
        assert refused.value.status_code == 409
    assert (
        await db_session.get(GenerationAllowance, consumed.id, populate_existing=True)
    ).status == "consumed"


async def test_paid_renewal_does_not_move_resumed_runs_or_spend_current_period(
    db_session: AsyncSession,
) -> None:
    owner, subscription = await _account(db_session)
    old = await _reserve(db_session, owner, "edit")
    old_bounds = subscription.current_period_start, subscription.current_period_end
    subscription.current_period_start = datetime.now(UTC) - timedelta(hours=1)
    subscription.current_period_end = datetime.now(UTC) + timedelta(days=30)
    await db_session.flush()
    assert await reserve_included_generation(
        db_session, user_id=owner.id, run_id=old.id, kind="edit"
    )
    await _consume(db_session, old)
    entry = await db_session.get(GenerationAllowance, old.id)
    assert (entry.period_start, entry.period_end) == old_bounds
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert usage["edits"]["remaining"] == 25
    assert usage["creations"]["remaining"] == 2


async def test_expired_paid_reserved_run_finishes_with_its_original_kind(
    db_session: AsyncSession,
) -> None:
    owner, subscription = await _account(db_session)
    run = await _reserve(db_session, owner, "edit")
    subscription.status = "expired"
    await db_session.flush()
    assert await reserve_included_generation(
        db_session, user_id=owner.id, run_id=run.id, kind="edit"
    )
    await _consume(db_session, run, mode="build")
    assert (await db_session.get(GenerationAllowance, run.id)).status == "consumed"


@pytest.mark.parametrize("state", ["expired", "future", "missing_start", "missing_end", "paused"])
async def test_paid_new_admission_requires_an_active_paid_period(
    db_session: AsyncSession,
    state: str,
) -> None:
    owner, subscription = await _account(db_session)
    if state == "expired":
        subscription.current_period_start = datetime.now(UTC) - timedelta(days=31)
        subscription.current_period_end = datetime.now(UTC) - timedelta(days=1)
    elif state == "future":
        subscription.current_period_start = datetime.now(UTC) + timedelta(days=1)
    elif state == "missing_start":
        subscription.current_period_start = None
    elif state == "missing_end":
        subscription.current_period_end = None
    else:
        subscription.status = "paused"
    run = await _run(db_session, owner)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(
            db_session, user_id=owner.id, run_id=run.id, kind="creation"
        )
    assert refused.value.status_code == 402


@pytest.mark.parametrize("at_expiry", [False, True])
async def test_paid_admission_uses_one_locked_timestamp_at_expiry(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    at_expiry: bool,
) -> None:
    from yleum_api.services import generation_allowance

    owner, subscription = await _account(db_session)
    run = await _run(db_session, owner)
    end = subscription.current_period_end
    first = end if at_expiry else end - timedelta(milliseconds=1)
    instants = iter((first, end))

    class ExpiryClock:
        @staticmethod
        def now(_zone: object) -> datetime:
            return next(instants, end)

    monkeypatch.setattr(generation_allowance, "datetime", ExpiryClock)
    if at_expiry:
        with pytest.raises(ApiError) as refused:
            await reserve_included_generation(
                db_session, user_id=owner.id, run_id=run.id, kind="edit"
            )
        assert refused.value.status_code == 402
    else:
        assert await reserve_included_generation(
            db_session, user_id=owner.id, run_id=run.id, kind="edit"
        )
        assert (await db_session.get(GenerationAllowance, run.id)).period_end == end
        assert await quota_usage(db_session, await load_plan_context(db_session, owner.id)) is None


@pytest.mark.parametrize("value", [None, True, "25"])
async def test_partial_new_terms_fail_closed_instead_of_falling_back_to_wallet(
    db_session: AsyncSession,
    value: object,
) -> None:
    owner, subscription = await _account(db_session)
    plan = await db_session.get(BillingPlan, subscription.plan_id)
    plan.entitlements = {**plan.entitlements, "included_edits": value}
    run = await _run(db_session, owner)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id, kind="edit")
    assert refused.value.status_code == 402


async def test_historical_contract_without_bucket_terms_keeps_its_original_path(
    db_session: AsyncSession,
) -> None:
    owner, subscription = await _account(db_session)
    plan = await db_session.get(BillingPlan, subscription.plan_id)
    plan.entitlements = {}
    run = await _run(db_session, owner)
    assert not await reserve_included_generation(
        db_session, user_id=owner.id, run_id=run.id, kind="edit"
    )
    assert await quota_usage(db_session, await load_plan_context(db_session, owner.id)) is None


async def test_worker_role_can_settle_both_kinds_but_cannot_insert_or_delete(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner, _ = await _account(db_session, "free")
    accepted = await _reserve(db_session, owner, "creation")
    failed = await _reserve(db_session, owner, "edit")
    accepted.status, accepted.response_mode = "completed", "build"
    failed.status, failed.response_mode = "failed", "build"
    await db_session.commit()
    role = f"qa_generation_allowance_{uuid4().hex}"
    admin = await asyncpg.connect(
        test_engine.url.render_as_string(hide_password=False).replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )
    )
    try:
        await admin.execute(
            f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT'
        )
        grants = await asyncio.to_thread(WORKER_GRANTS.read_text, encoding="utf-8")
        await admin.execute(grants.replace(':"billing_role"', f'"{role}"'))
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as worker:
            await worker.execute(text(f'SET LOCAL ROLE "{role}"'))
            for run_id, verdict in ((accepted.id, True), (failed.id, False)):
                run = await worker.get(GenerationRun, run_id, with_for_update=True)
                await settle_included_generation(worker, run, accepted=verdict)
            await worker.commit()
        for statement in (
            "INSERT INTO generation_allowances SELECT * FROM generation_allowances WHERE false",
            "DELETE FROM generation_allowances WHERE false",
        ):
            async with factory() as worker:
                await worker.execute(text(f'SET LOCAL ROLE "{role}"'))
                with pytest.raises(DBAPIError) as refused:
                    await worker.execute(text(statement))
                assert refused.value.orig.sqlstate == "42501"
        assert (
            await admin.fetchval(
                "SELECT status FROM generation_allowances WHERE run_id=$1", accepted.id
            )
            == "consumed"
        )
        assert (
            await admin.fetchval(
                "SELECT status FROM generation_allowances WHERE run_id=$1", failed.id
            )
            == "released"
        )
    finally:
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


async def test_free_admission_reloads_a_plan_changed_while_waiting_for_the_owner_lock(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services import generation_allowance

    owner, subscription = await _account(db_session, "free")
    paid_plan = await db_session.scalar(
        select(BillingPlan).where(BillingPlan.code == "pro", BillingPlan.is_active.is_(True))
    )
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    context_read = asyncio.Event()
    real_load = generation_allowance.load_plan_context

    async def observe_context(session: AsyncSession, user_id):
        context = await real_load(session, user_id)
        context_read.set()
        return context

    monkeypatch.setattr(generation_allowance, "load_plan_context", observe_context)
    async with factory() as updater:
        await updater.execute(
            select(User.id).where(User.id == owner.id).with_for_update(key_share=True)
        )

        async def admit() -> None:
            async with factory() as session:
                run = await _run(session, owner)
                with pytest.raises(ApiError) as refused:
                    await reserve_included_generation(
                        session, user_id=owner.id, run_id=run.id, kind="creation"
                    )
                assert refused.value.code == "conflict"
                assert refused.value.status_code == 409

        pending = asyncio.create_task(admit())
        try:
            await asyncio.wait_for(context_read.wait(), timeout=10)
            changed = await updater.get(Subscription, subscription.id)
            changed.plan_id = paid_plan.id
            changed.current_period_start = datetime.now(UTC) - timedelta(hours=1)
            changed.current_period_end = datetime.now(UTC) + timedelta(days=30)
            await updater.commit()
            await asyncio.wait_for(pending, timeout=10)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    assert not (await db_session.scalars(select(GenerationAllowance))).all()


async def test_malformed_allowance_period_fails_closed(db_session: AsyncSession) -> None:
    owner, subscription = await _account(db_session)
    plan = await db_session.get(BillingPlan, subscription.plan_id)
    plan.entitlements = {**plan.entitlements, "generation_allowance_period": []}
    run = await _run(db_session, owner)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id, kind="edit")
    assert refused.value.status_code == 402
