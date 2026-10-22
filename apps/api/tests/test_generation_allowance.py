from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
WORKER_GRANTS = (
    Path(__file__).resolve().parents[3]
    / "infra/max-k3s/commerce/remote/billing-generation-grants.sql"
)


async def _paid_account(session: AsyncSession) -> tuple[User, Subscription]:
    owner = User(email=f"allowance-{uuid4().hex}@example.com")
    session.add(owner)
    await session.flush()
    account = BillingAccount(personal_user_id=owner.id, created_by_user_id=owner.id)
    session.add(account)
    plan = await session.scalar(
        select(BillingPlan).where(BillingPlan.code == "pro", BillingPlan.is_active.is_(True))
    )
    assert plan is not None
    plan.entitlements = {**plan.entitlements, "included_generations": 5}
    await session.flush()
    now = datetime.now(UTC)
    subscription = Subscription(
        billing_account_id=account.id,
        user_id=owner.id,
        plan_id=plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=29),
    )
    session.add(subscription)
    await session.flush()
    return owner, subscription


async def _run(
    session: AsyncSession,
    owner: User,
    *,
    project: Project | None = None,
) -> GenerationRun:
    if project is None:
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


async def test_create_and_edits_share_five_generation_account_limit(
    db_session: AsyncSession,
) -> None:
    owner, subscription = await _paid_account(db_session)
    first = await _run(db_session, owner)
    project = await db_session.get(Project, first.project_id)
    for index in range(5):
        run = first if index == 0 else await _run(db_session, owner, project=project)
        assert await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
        run.status = "completed"
        run.response_mode = "build" if index == 0 else "edit"
        await settle_included_generation(db_session, run, accepted=True)
        await db_session.flush()

    context = await load_plan_context(db_session, owner.id)
    assert await quota_usage(db_session, context) == {
        "limit": 5,
        "used": 5,
        "in_progress": 0,
        "remaining": 0,
        "period_start": subscription.current_period_start,
        "period_end": subscription.current_period_end,
    }
    sixth = await _run(db_session, owner, project=project)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=sixth.id)
    assert refused.value.status_code == 402
    assert refused.value.code == "entitlement_exceeded"
    assert refused.value.details["entitlement"] == "included_generations"


async def test_concurrent_admission_reserves_at_most_five_without_fk_lock_deadlock(
    db_session: AsyncSession,
    test_engine,
) -> None:
    owner, subscription = await _paid_account(db_session)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ready = asyncio.Barrier(8)

    async def admit() -> bool:
        async with factory() as session:
            run = await _run(session, owner)
            # Model the KEY SHARE lock an earlier FK insert can already hold.
            await session.execute(
                select(Subscription.id)
                .where(Subscription.id == subscription.id)
                .with_for_update(read=True, key_share=True)
            )
            await ready.wait()
            try:
                result = await reserve_included_generation(session, user_id=owner.id, run_id=run.id)
                await session.commit()
                return result
            except ApiError as error:
                assert error.status_code == 402
                return False

    results = await asyncio.wait_for(asyncio.gather(*(admit() for _ in range(8))), timeout=15)
    assert results.count(True) == 5
    reservations = (await db_session.scalars(select(GenerationAllowance))).all()
    assert len(reservations) == 5
    assert {entry.status for entry in reservations} == {"reserved"}


async def test_reserved_replay_and_terminal_settlement_are_idempotent(
    db_session: AsyncSession,
) -> None:
    owner, _ = await _paid_account(db_session)
    run = await _run(db_session, owner)
    assert await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    assert await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    await settle_included_generation(db_session, run, accepted=False)
    entry = await db_session.get(GenerationAllowance, run.id)
    assert entry.status == "reserved"
    assert entry.settled_at is None
    run.status, run.response_mode = "completed", "build"
    await settle_included_generation(db_session, run, accepted=True)
    settled_at = entry.settled_at
    await settle_included_generation(db_session, run, accepted=False)
    assert await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    assert entry.status == "consumed"
    assert entry.settled_at == settled_at
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert (usage["used"], usage["in_progress"], usage["remaining"]) == (1, 0, 4)


@pytest.mark.parametrize(
    ("status", "mode"),
    [("failed", "build"), ("cancelled", "edit"), ("completed", "advice")],
)
async def test_unsuccessful_or_nonbuild_terminal_run_releases_only_its_reservation(
    db_session: AsyncSession,
    status: str,
    mode: str,
) -> None:
    owner, _ = await _paid_account(db_session)
    runs = [await _run(db_session, owner) for _ in range(5)]
    for run in runs:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    runs[0].status, runs[0].response_mode = status, mode
    await settle_included_generation(db_session, runs[0], accepted=False)
    await settle_included_generation(db_session, runs[0], accepted=True)
    assert (await db_session.get(GenerationAllowance, runs[0].id)).status == "released"
    context = await load_plan_context(db_session, owner.id)
    usage = await quota_usage(db_session, context)
    assert (usage["used"], usage["in_progress"], usage["remaining"]) == (0, 4, 1)
    with pytest.raises(ApiError) as replay:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=runs[0].id)
    assert replay.value.status_code == 409
    replacement = await _run(db_session, owner)
    assert await reserve_included_generation(db_session, user_id=owner.id, run_id=replacement.id)


async def test_period_renewal_does_not_move_or_recharge_resumed_runs(
    db_session: AsyncSession,
) -> None:
    owner, subscription = await _paid_account(db_session)
    old_start, old_end = subscription.current_period_start, subscription.current_period_end
    old = await _run(db_session, owner)
    await reserve_included_generation(db_session, user_id=owner.id, run_id=old.id)
    subscription.current_period_start = datetime.now(UTC) - timedelta(hours=1)
    subscription.current_period_end = datetime.now(UTC) + timedelta(days=30)
    await db_session.flush()
    assert await reserve_included_generation(db_session, user_id=owner.id, run_id=old.id)
    old.status, old.response_mode = "completed", "edit"
    await settle_included_generation(db_session, old, accepted=True)
    historical = await db_session.get(GenerationAllowance, old.id)
    assert (historical.period_start, historical.period_end) == (old_start, old_end)
    assert historical.status == "consumed"
    for _ in range(5):
        current = await _run(db_session, owner)
        assert await reserve_included_generation(db_session, user_id=owner.id, run_id=current.id)
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert (usage["used"], usage["in_progress"], usage["remaining"]) == (0, 5, 0)


async def test_project_and_run_deletion_preserve_consumed_account_quota(
    db_session: AsyncSession,
) -> None:
    owner, _ = await _paid_account(db_session)
    run = await _run(db_session, owner)
    await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    run.status, run.response_mode = "completed", "build"
    await settle_included_generation(db_session, run, accepted=True)
    await db_session.execute(delete(Project).where(Project.id == run.project_id))
    await db_session.commit()
    remaining_run = await db_session.scalar(
        select(GenerationRun.id).where(GenerationRun.id == run.id)
    )
    assert remaining_run is None
    assert (await db_session.get(GenerationAllowance, run.id)).status == "consumed"
    usage = await quota_usage(db_session, await load_plan_context(db_session, owner.id))
    assert (usage["used"], usage["remaining"]) == (1, 4)


@pytest.mark.parametrize("state", ["expired", "future", "missing_start", "missing_end", "paused"])
async def test_new_reservations_require_an_active_paid_period(
    db_session: AsyncSession,
    state: str,
) -> None:
    owner, subscription = await _paid_account(db_session)
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
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    assert refused.value.status_code == 402
    assert refused.value.code == "subscription_entitlement_required"


async def test_expired_reserved_run_can_resume_and_settle_without_new_admission(
    db_session: AsyncSession,
) -> None:
    owner, subscription = await _paid_account(db_session)
    run = await _run(db_session, owner)
    await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    subscription.current_period_start = datetime.now(UTC) - timedelta(days=31)
    subscription.current_period_end = datetime.now(UTC) - timedelta(days=1)
    subscription.status = "expired"
    await db_session.flush()
    assert await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    run.status, run.response_mode = "completed", "build"
    await settle_included_generation(db_session, run, accepted=True)
    assert (await db_session.get(GenerationAllowance, run.id)).status == "consumed"


@pytest.mark.parametrize("value", [None, True, "5"])
async def test_plans_without_numeric_generation_allowance_keep_existing_billing_path(
    db_session: AsyncSession,
    value: object,
) -> None:
    owner, subscription = await _paid_account(db_session)
    plan = await db_session.get(BillingPlan, subscription.plan_id)
    plan.entitlements = {**plan.entitlements, "included_generations": value}
    run = await _run(db_session, owner)
    assert not await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    assert await db_session.get(GenerationAllowance, run.id) is None
    assert await quota_usage(db_session, await load_plan_context(db_session, owner.id)) is None


async def test_numeric_entitlement_on_free_plan_cannot_enable_paid_included_generations(
    db_session: AsyncSession,
) -> None:
    owner, subscription = await _paid_account(db_session)
    plan = await db_session.get(BillingPlan, subscription.plan_id)
    plan.price_rub = 0
    run = await _run(db_session, owner)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    assert refused.value.status_code == 402


async def test_replay_cannot_claim_another_users_allowance(db_session: AsyncSession) -> None:
    owner, _ = await _paid_account(db_session)
    run = await _run(db_session, owner)
    await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    other, _ = await _paid_account(db_session)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(db_session, user_id=other.id, run_id=run.id)
    assert refused.value.status_code == 409


async def test_replay_rejects_a_ledger_with_mismatched_billing_account(
    db_session: AsyncSession,
) -> None:
    owner, _ = await _paid_account(db_session)
    run = await _run(db_session, owner)
    await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    _, other_subscription = await _paid_account(db_session)
    entry = await db_session.get(GenerationAllowance, run.id)
    entry.billing_account_id = other_subscription.billing_account_id
    await db_session.flush()
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    assert refused.value.status_code == 409


async def test_concurrent_replay_reserves_the_same_run_once(
    db_session: AsyncSession,
    test_engine,
) -> None:
    owner, _ = await _paid_account(db_session)
    run = await _run(db_session, owner)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ready = asyncio.Barrier(4)

    async def replay() -> bool:
        async with factory() as session:
            await ready.wait()
            included = await reserve_included_generation(session, user_id=owner.id, run_id=run.id)
            await session.commit()
            return included

    assert await asyncio.wait_for(asyncio.gather(*(replay() for _ in range(4))), timeout=15) == [
        True,
        True,
        True,
        True,
    ]
    assert len((await db_session.scalars(select(GenerationAllowance))).all()) == 1


async def test_another_accounts_allowance_is_independent(db_session: AsyncSession) -> None:
    first, _ = await _paid_account(db_session)
    for _ in range(5):
        run = await _run(db_session, first)
        await reserve_included_generation(db_session, user_id=first.id, run_id=run.id)
    second, _ = await _paid_account(db_session)
    run = await _run(db_session, second)
    assert await reserve_included_generation(db_session, user_id=second.id, run_id=run.id)
    usage = await quota_usage(db_session, await load_plan_context(db_session, second.id))
    assert (usage["used"], usage["in_progress"], usage["remaining"]) == (0, 1, 4)


@pytest.mark.parametrize("value", [0, -1])
async def test_zero_or_negative_generation_limit_never_admits_a_run(
    db_session: AsyncSession,
    value: int,
) -> None:
    owner, subscription = await _paid_account(db_session)
    plan = await db_session.get(BillingPlan, subscription.plan_id)
    plan.entitlements = {**plan.entitlements, "included_generations": value}
    run = await _run(db_session, owner)
    with pytest.raises(ApiError) as refused:
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    assert refused.value.status_code == 402


@pytest.mark.parametrize(
    ("status", "mode"),
    [("failed", "build"), ("cancelled", "edit"), ("completed", "advice")],
)
async def test_contradictory_accepted_verdict_cannot_consume_an_allowance(
    db_session: AsyncSession,
    status: str,
    mode: str,
) -> None:
    owner, _ = await _paid_account(db_session)
    run = await _run(db_session, owner)
    await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    run.status, run.response_mode = status, mode
    with pytest.raises(ValueError, match="accepted completed build"):
        await settle_included_generation(db_session, run, accepted=True)
    assert (await db_session.get(GenerationAllowance, run.id)).status == "reserved"


async def test_worker_grants_settle_allowances_without_allowing_insert_or_delete(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner, _ = await _paid_account(db_session)
    accepted = await _run(db_session, owner)
    failed = await _run(db_session, owner)
    for run in (accepted, failed):
        await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    accepted.status, accepted.response_mode = "completed", "build"
    failed.status, failed.response_mode = "failed", "edit"
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
                assert run is not None
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
        # The exact UUID role is created above and owns no application objects.
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


async def test_stale_session_cannot_reverse_a_committed_terminal_settlement(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner, _ = await _paid_account(db_session)
    run = await _run(db_session, owner)
    await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    run.status, run.response_mode = "completed", "build"
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as stale, factory() as first:
        cached = await stale.get(GenerationAllowance, run.id)
        assert cached.status == "reserved"
        first_run = await first.get(GenerationRun, run.id, with_for_update=True)
        await settle_included_generation(first, first_run, accepted=True)
        await first.commit()
        stale_run = await stale.get(GenerationRun, run.id, with_for_update=True)
        await settle_included_generation(stale, stale_run, accepted=False)
        await stale.commit()
    entry = await db_session.get(GenerationAllowance, run.id, populate_existing=True)
    assert entry.status == "consumed"


async def test_stale_replay_cannot_restart_a_released_generation(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner, _ = await _paid_account(db_session)
    run = await _run(db_session, owner)
    await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as stale, factory() as terminal:
        cached = await stale.get(GenerationAllowance, run.id)
        assert cached.status == "reserved"
        terminal_run = await terminal.get(GenerationRun, run.id, with_for_update=True)
        terminal_run.status = "failed"
        await settle_included_generation(terminal, terminal_run, accepted=False)
        await terminal.commit()
        with pytest.raises(ApiError) as refused:
            await reserve_included_generation(stale, user_id=owner.id, run_id=run.id)
        assert refused.value.status_code == 409


@pytest.mark.parametrize("at_expiry", [False, True])
async def test_admission_uses_one_locked_period_timestamp_at_expiry(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    at_expiry: bool,
) -> None:
    from yleum_api.services import generation_allowance

    owner, subscription = await _paid_account(db_session)
    run = await _run(db_session, owner)
    end = subscription.current_period_end
    assert end is not None
    first = end if at_expiry else end - timedelta(milliseconds=1)
    instants = iter((first, end))

    class ExpiryClock:
        @staticmethod
        def now(_zone: object) -> datetime:
            return next(instants, end)

    monkeypatch.setattr(generation_allowance, "datetime", ExpiryClock)
    if at_expiry:
        with pytest.raises(ApiError) as refused:
            await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
        assert refused.value.status_code == 402
        assert refused.value.code == "subscription_entitlement_required"
        assert await db_session.get(GenerationAllowance, run.id) is None
    else:
        assert await reserve_included_generation(db_session, user_id=owner.id, run_id=run.id)
        entry = await db_session.get(GenerationAllowance, run.id)
        assert entry.status == "reserved"
        assert entry.period_end == end
        # Later reads see expiry, without invalidating the earlier admission.
        context = await load_plan_context(db_session, owner.id)
        assert await quota_usage(db_session, context) is None
