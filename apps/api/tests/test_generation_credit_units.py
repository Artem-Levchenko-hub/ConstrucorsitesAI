from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.models.account import Payment
from yleum_api.models.billing import FREE_PLAN_ID, BillingAccount, Subscription
from yleum_api.models.generation_credits import GenerationCreditGrant, GenerationCreditReservation
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.services import generation_credits as credits
from yleum_api.services.generation_credits import allocate_units


def test_edit_prefers_included_then_edit_only_before_flexible():
    assert allocate_units(
        [
            {"id": "included", "bucket": "edit", "available": 1},
            {"id": "starter", "bucket": "edit_only", "available": 5},
            {"id": "flex", "bucket": "flexible", "available": 15},
        ],
        "edit",
    ) == [{"grant_id": "included", "units": 1}]


def test_initial_cannot_use_starter_and_costs_five_flexible_units():
    assert allocate_units(
        [
            {"id": "starter", "bucket": "edit_only", "available": 5},
            {"id": "flex", "bucket": "flexible", "available": 5},
        ],
        "initial",
    ) == [{"grant_id": "flex", "units": 5}]


def test_initial_does_not_partially_spend_insufficient_flexible_units():
    with pytest.raises(ValueError):
        allocate_units(
            [
                {"id": "starter", "bucket": "edit_only", "available": 100},
                {"id": "flex", "bucket": "flexible", "available": 4},
            ],
            "initial",
        )


async def owner(session):
    user = User(is_anon=True)
    session.add(user)
    await session.flush()
    account = BillingAccount(personal_user_id=user.id, created_by_user_id=user.id)
    session.add(account)
    await session.flush()
    session.add(
        Subscription(
            billing_account_id=account.id, user_id=user.id, plan_id=FREE_PLAN_ID, status="active"
        )
    )
    await session.flush()
    return user, account


async def run_for(session, user, project=None):
    if project is None:
        project = Project(
            owner_id=user.id, name="Synthetic", slug="units-" + uuid4().hex, template="blank"
        )
        session.add(project)
        await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=user.id,
        idempotency_key=uuid4().hex,
        prompt_hash="a" * 64,
        status="running",
    )
    session.add(run)
    await session.flush()
    return run, project


async def payment(session, user, account, units=5, edits_only=False):
    value = Payment(
        billing_account_id=account.id,
        user_id=user.id,
        idempotency_key=uuid4().hex,
        package_code="synthetic",
        amount_rub=Decimal(1),
        credit_rub=Decimal(0),
        status="succeeded",
        edit_units=units,
        edits_only=edits_only,
        package_version=2,
    )
    session.add(value)
    await session.flush()
    await credits.grant_payment_units(session, value)
    return value


@pytest.mark.asyncio
async def test_duplicate_verified_payment_grants_once(db_session):
    user, account = await owner(db_session)
    value = await payment(db_session, user, account)
    await credits.grant_payment_units(db_session, value)
    grants = (
        await db_session.scalars(
            select(GenerationCreditGrant).where(GenerationCreditGrant.payment_id == value.id)
        )
    ).all()
    assert len(grants) == 1 and grants[0].units == 5
    assert value.credit_rub == 0


@pytest.mark.asyncio
async def test_failed_initial_releases_once_and_replay_never_spends_twice(db_session):
    user, _account = await owner(db_session)
    run, project = await run_for(db_session, user)
    hold = await credits.reserve_generation_units(db_session, run)
    assert await credits.reserve_generation_units(db_session, run) is hold
    assert hold.kind == "initial" and hold.units == 1
    run.status = "failed"
    await credits.settle_generation_units(db_session, run, accepted=False, snapshot_id=None)
    await credits.settle_generation_units(db_session, run, accepted=False, snapshot_id=None)
    view = await credits.credits_view(db_session, user.id)
    assert view["included_generations_remaining"] == 1 and view["reserved_units"] == 0
    retry, _ = await run_for(db_session, user, project)
    assert (await credits.reserve_generation_units(db_session, retry)).kind == "initial"


@pytest.mark.asyncio
async def test_exhausted_free_starter_unlocks_edit_but_never_initial(db_session):
    user, account = await owner(db_session)
    user.free_generations_used = 1
    run, project = await run_for(db_session, user)
    project.generation_initial_accepted = True
    await credits.reserve_generation_units(db_session, run)
    run.status = "completed"
    await credits.settle_generation_units(db_session, run, accepted=True, snapshot_id=uuid4())
    await payment(db_session, user, account, edits_only=True)
    edit, _ = await run_for(db_session, user, project)
    edit_hold = await credits.reserve_generation_units(db_session, edit)
    grant = await db_session.get(GenerationCreditGrant, edit_hold.allocations[0]["grant_id"])
    assert grant.bucket == "edit_only" and edit_hold.units == 1
    initial, _ = await run_for(db_session, user)
    with pytest.raises(ApiError):
        await credits.reserve_generation_units(db_session, initial)


@pytest.mark.asyncio
async def test_two_projects_cannot_reserve_same_last_five_flexible_units(db_session, test_engine):
    import asyncio

    user, account = await owner(db_session)
    user.free_generations_used = 1
    await payment(db_session, user, account)
    left, _ = await run_for(db_session, user)
    right, _ = await run_for(db_session, user)
    ids = [left.id, right.id]
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def reserve(run_id):
        async with factory() as session:
            row = await session.get(GenerationRun, run_id)
            try:
                hold = await credits.reserve_generation_units(session, row)
                await session.commit()
                return hold.units
            except ApiError:
                await session.rollback()
                return "denied"

    result = await asyncio.wait_for(asyncio.gather(*(reserve(run_id) for run_id in ids)), 10)
    assert sorted(result, key=str) == sorted([5, "denied"], key=str)


@pytest.mark.asyncio
async def test_only_verified_paid_period_grants_and_expiry_never_refills(db_session, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from yleum_api.models.billing import PRO_PLAN_ID, BillingPlan

    user, account = await owner(db_session)
    sub = await db_session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    sub.plan_id = PRO_PLAN_ID
    start = datetime.now(UTC)
    sub.current_period_start = start
    sub.current_period_end = start + timedelta(days=30)
    plan = await db_session.get(BillingPlan, PRO_PLAN_ID)
    value = Payment(
        billing_account_id=account.id,
        user_id=user.id,
        idempotency_key=uuid4().hex,
        package_code="subscription:pro:v3",
        purpose="subscription_initial",
        subscription_id=sub.id,
        amount_rub=Decimal(1),
        credit_rub=Decimal(0),
        status="pending",
    )
    db_session.add(value)
    await db_session.flush()
    with pytest.raises(ValueError):
        await credits.grant_subscription_units(db_session, sub, value, plan)
    assert (await credits.credits_view(db_session, user.id))["included_generations_remaining"] == 0
    value.status = "succeeded"
    await credits.grant_subscription_units(db_session, sub, value, plan)
    await credits.grant_subscription_units(db_session, sub, value, plan)
    initial, _project = await run_for(db_session, user)
    hold = await credits.reserve_generation_units(db_session, initial)
    original = list(hold.allocations)
    end = sub.current_period_end

    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return end + timedelta(seconds=1)

    monkeypatch.setattr(credits, "datetime", Future)
    assert (await credits.credits_view(db_session, user.id))["included_generations_remaining"] == 0
    initial.status = "completed"
    await credits.settle_generation_units(db_session, initial, accepted=True, snapshot_id=uuid4())
    assert hold.state == "consumed" and hold.allocations == original
    sub.current_period_start = end
    sub.current_period_end = end + timedelta(days=30)
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
    db_session.add(renewal)
    await db_session.flush()
    await credits.grant_subscription_units(db_session, sub, renewal, plan)
    view = await credits.credits_view(db_session, user.id)
    assert view["included_generations_remaining"] == 2 and view["included_edits_remaining"] == 25


@pytest.mark.asyncio
async def test_cancelled_hold_cannot_later_accept_and_keep_returned_units(db_session):
    user, _account = await owner(db_session)
    run, _project = await run_for(db_session, user)
    await credits.reserve_generation_units(db_session, run)
    run.status = "cancelled"
    await credits.settle_generation_units(db_session, run, accepted=False, snapshot_id=None)
    with pytest.raises(ValueError):
        await credits.settle_generation_units(db_session, run, accepted=True, snapshot_id=uuid4())


@pytest.mark.asyncio
async def test_other_account_cannot_reuse_or_settle_a_hold(db_session):
    user, _account = await owner(db_session)
    run, _project = await run_for(db_session, user)
    await credits.reserve_generation_units(db_session, run)
    other, _other_account = await owner(db_session)
    run.user_id = other.id
    with pytest.raises(ApiError) as failure:
        await credits.reserve_generation_units(db_session, run)
    assert failure.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["build", "edit"])
async def test_real_gateway_expense_and_accepted_build_consume_units_without_rub_debit(
    db_session, test_engine, monkeypatch, mode
):
    from pathlib import Path
    from types import SimpleNamespace

    import asyncpg

    apps = Path(__file__).parents[2]
    monkeypatch.syspath_prepend(str(apps / "llm-gateway/src"))
    monkeypatch.syspath_prepend(str(apps / "llm-gateway/integration_tests"))
    from test_generation_billing_postgres import _accepted
    from yleum_gateway.services import billing, generation_attempts, provider_calls
    from yleum_gateway.services.generation_identity import GenerationProviderIdentity

    from yleum_api.models.message import Message
    from yleum_api.models.usage import Usage
    from yleum_api.models.wallet_charge import WalletCharge
    from yleum_api.services.generation_billing import admit_policy, seal_terminal_locked

    user, _account = await owner(db_session)
    run, project = await run_for(db_session, user)
    message = Message(project_id=project.id, role="assistant", content="synthetic")
    db_session.add(message)
    await db_session.flush()
    run.assistant_message_id = message.id
    run.response_mode = mode
    project.generation_initial_accepted = mode == "edit"
    await credits.reserve_generation_units(db_session, run)
    await admit_policy(db_session, run.id, is_free=False, unit_funded=True)
    await db_session.commit()
    from yleum_api.services.generation import provider_attempts as api_attempts

    monkeypatch.setattr(api_attempts, "get_engine", lambda: test_engine)
    monkeypatch.setattr(
        api_attempts,
        "get_settings",
        lambda: SimpleNamespace(
            generation_provider_attempts_enabled=True,
            generation_response_retention_enabled=True,
            generation_recovery_mode="off",
        ),
    )
    body = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "synthetic"}],
        "metadata": {"run_id": str(run.id), "project_id": str(project.id), "user_id": str(user.id)},
    }
    api_identity = await api_attempts.prepare_generation_attempt(body, "/v1/messages")
    assert api_identity is not None
    identity = GenerationProviderIdentity.from_wire(api_identity.to_wire())
    pool = await asyncpg.create_pool(
        test_engine.url.render_as_string(hide_password=False).replace(
            "postgresql+asyncpg://", "postgresql://"
        ),
        min_size=1,
        max_size=2,
    )
    try:
        for module in (billing, generation_attempts, provider_calls):
            monkeypatch.setattr(module, "get_pool", lambda: pool)
        monkeypatch.setattr(
            generation_attempts,
            "get_settings",
            lambda: SimpleNamespace(generation_provider_attempts_enabled=True),
        )
        assert await billing.precheck_generation_units(
            user.id, project.id, run.id, "native_agent", identity
        )
        call = await generation_attempts.dispatch(
            identity,
            route="/v1/messages",
            model="claude-sonnet-5.5",
            user_id=user.id,
            project_id=project.id,
            run_id=run.id,
            stage="native_agent",
            free=False,
        )
        replay_identity = await api_attempts.prepare_generation_attempt(body, "/v1/messages")
        assert replay_identity == api_identity
        assert (await generation_attempts.reserve_generation_attempt(identity)).action == "pending"
        await generation_attempts.finish(identity, call, status="failed", reason="upstream_refused")
        child = await api_attempts.prepare_generation_attempt(
            body, "/v1/messages", parent=api_identity
        )
        assert child is not None and child.attempt_id != api_identity.attempt_id
        assert (
            await api_attempts.prepare_generation_attempt(body, "/v1/messages", parent=api_identity)
            == child
        )
        identity = GenerationProviderIdentity.from_wire(child.to_wire())
        assert await billing.precheck_generation_units(
            user.id, project.id, run.id, "native_agent", identity
        )
        call = await generation_attempts.dispatch(
            identity,
            route="/v1/messages",
            model="claude-sonnet-5.5",
            user_id=user.id,
            project_id=project.id,
            run_id=run.id,
            stage="native_agent",
            free=False,
            provider_scope="aitunnel",
        )
        # An unfinished backup cannot authorize another child.
        with pytest.raises(RuntimeError):
            await api_attempts.prepare_generation_attempt(body, "/v1/messages", parent=child)
        await generation_attempts.finish(
            identity,
            call,
            status="completed",
            actual_model="claude-sonnet-5.5",
            tokens_in=2,
            tokens_out=3,
            cache_read_tokens=0,
            cache_write_tokens=0,
            calculated_cost_rub=Decimal(1),
        )
        kwargs = dict(
            user_id=user.id,
            project_id=project.id,
            run_id=run.id,
            message_id=message.id,
            model_id="claude-sonnet-5.5",
            tokens_in=2,
            tokens_out=3,
            cost_rub=Decimal(1),
            description="synthetic",
            stage="native_agent",
            free=False,
            provider_binding=billing.ProviderUsageBinding(identity, call),
            provider_scope="aitunnel",
        )
        # Receipt message must match the admitted provider call.
        kwargs["message_id"] = None
        first = await billing.charge(**kwargs)
        assert await billing.charge(**kwargs) == first
        assert (
            len((await db_session.scalars(select(Usage).where(Usage.run_id == run.id))).all()) == 1
        )
        assert not (await db_session.scalars(select(WalletCharge))).all()
        await _accepted(db_session, user.id, project.id, run.id, [message.id])
        run.status = "completed"
        await seal_terminal_locked(db_session, run)
        await seal_terminal_locked(db_session, run)
        hold = await db_session.get(GenerationCreditReservation, run.id)
        assert hold.state == "consumed"
        from datetime import UTC, datetime

        from yleum_api.services.generation.recovery_public import project_generation_public

        run.finished_at = datetime.now(UTC)
        await db_session.flush()
        public = await project_generation_public(
            db_session, project_id=project.id, user_id=user.id, run_id=run.id
        )
        assert public.acceptance.current_request_complete
        assert public.acceptance.snapshot_id == hold.accepted_snapshot_id
        from yleum_api.models.generation_billing import (
            GenerationBillingIntent,
            GenerationBillingPolicy,
        )
        from yleum_api.models.snapshot import Snapshot
        from yleum_api.services.generation.recovery_public import _legacy_accepted

        snapshot = await db_session.get(Snapshot, hold.accepted_snapshot_id)
        policy = await db_session.get(GenerationBillingPolicy, run.id)
        intent = await db_session.get(GenerationBillingIntent, run.id)
        assert not _legacy_accepted(run, project, message, snapshot, policy, None, intent)
        receipt = intent.receipt
        intent.receipt = {"policy": "accepted-units-v2", "units": "released"}
        assert not _legacy_accepted(run, project, message, snapshot, policy, hold, intent)
        intent.receipt = receipt
        assert not (await db_session.scalars(select(WalletCharge))).all()
        await db_session.commit()
        assert await billing.charge(**kwargs) == first
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_terminal_seal_refuses_late_accept_after_cancel_refund(db_session):
    from yleum_api.services.generation_billing import admit_policy, seal_terminal_locked

    user, _account = await owner(db_session)
    run, _project = await run_for(db_session, user)
    await credits.reserve_generation_units(db_session, run)
    await admit_policy(db_session, run.id, is_free=False, unit_funded=True)
    run.status = "cancelled"
    await seal_terminal_locked(db_session, run)
    run.status = "completed"
    run.response_mode = "build"
    run.agent_state = {"billing_accepted_candidate": {"snapshot_id": str(uuid4())}}
    with pytest.raises(RuntimeError, match="Late accepted"):
        await seal_terminal_locked(db_session, run)


@pytest.mark.asyncio
async def test_period_end_and_terminal_state_are_database_enforced(db_session):
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError

    user, account = await owner(db_session)
    value = await payment(db_session, user, account)
    sub = await db_session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                GenerationCreditGrant(
                    billing_account_id=account.id,
                    bucket="initial",
                    scope="period",
                    units=2,
                    external_ref="bad-period",
                    payment_id=value.id,
                    subscription_id=sub.id,
                    period_start=datetime.now(UTC),
                    period_end=None,
                )
            )
            await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                GenerationCreditReservation(
                    run_id=uuid4(),
                    billing_account_id=account.id,
                    user_id=user.id,
                    project_id=uuid4(),
                    kind="edit",
                    state="consumed",
                    units=1,
                    allocations=[{"grant_id": str(uuid4()), "units": 1}],
                )
            )
            await db_session.flush()


@pytest.mark.asyncio
async def test_unit_funded_noncaptured_run_registers_exact_identity_without_global_recovery(
    db_session, test_engine, monkeypatch
):
    from types import SimpleNamespace

    from yleum_api.services.generation import provider_attempts

    user, _account = await owner(db_session)
    run, project = await run_for(db_session, user)
    from yleum_api.services.generation_billing import admit_policy

    await credits.reserve_generation_units(db_session, run)
    await admit_policy(db_session, run.id, is_free=False, unit_funded=True)
    assert not (run.agent_state or {}).get("recovery_v1")
    await db_session.commit()
    monkeypatch.setattr(provider_attempts, "get_engine", lambda: test_engine)
    monkeypatch.setattr(
        provider_attempts,
        "get_settings",
        lambda: SimpleNamespace(
            generation_provider_attempts_enabled=True,
            generation_response_retention_enabled=True,
            generation_recovery_mode="off",
        ),
    )
    body = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "synthetic"}],
        "metadata": {"run_id": str(run.id), "project_id": str(project.id), "user_id": str(user.id)},
    }
    first = await provider_attempts.prepare_generation_attempt(body, "/v1/messages")
    assert first is not None
    again = await provider_attempts.prepare_generation_attempt(body, "/v1/messages")
    assert again == first
    malformed = dict(body)
    malformed["metadata"] = {**body["metadata"], "user_id": str(uuid4())}
    with pytest.raises(RuntimeError):
        await provider_attempts.prepare_generation_attempt(malformed, "/v1/messages")

    # A new tool transcript is another logical call, while identical unknown
    # outcomes reuse the same committed attempt rather than minting permission.
    tool_body = {
        **body,
        "messages": [*body["messages"], {"role": "assistant", "content": "tool result"}],
    }
    tool_identity = await provider_attempts.prepare_generation_attempt(tool_body, "/v1/messages")
    assert tool_identity is not None and tool_identity.attempt_id != first.attempt_id
    with pytest.raises(RuntimeError):
        await provider_attempts.prepare_generation_attempt(
            tool_body, "/v1/messages", existing=first
        )
    with pytest.raises(RuntimeError):
        await provider_attempts.prepare_generation_attempt(body, "/v1/messages", parent=first)
    monkeypatch.setattr(
        provider_attempts,
        "get_settings",
        lambda: SimpleNamespace(
            generation_provider_attempts_enabled=False,
            generation_response_retention_enabled=True,
            generation_recovery_mode="off",
        ),
    )
    with pytest.raises(RuntimeError):
        await provider_attempts.prepare_generation_attempt(body, "/v1/messages")


@pytest.mark.asyncio
async def test_profile_reports_metered_units_but_preserves_legacy_nullable_counters(
    client, db_session
):
    from tests.account_fixture import register_verified_account
    from yleum_api.models.billing import PRO_PLAN_V2_ID

    await register_verified_account(client, "unit-profile@example.com")
    profile = (await client.get("/api/auth/me")).json()
    assert profile["generation_initials_available"] == 1
    assert profile["generation_credit_units_available"] == 1
    user = await db_session.scalar(select(User).where(User.email == "unit-profile@example.com"))
    subscription = await db_session.scalar(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription.plan_id = PRO_PLAN_V2_ID
    await db_session.commit()
    legacy = (await client.get("/api/auth/me")).json()
    assert legacy["generation_initials_available"] is None
    assert legacy["generation_credit_units_available"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["edit", "text", "clarify"])
async def test_historical_accepted_edit_never_regrants_free_initial_or_edit(db_session, mode):
    user, _account = await owner(db_session)
    run, _project = await run_for(db_session, user)
    run.status = "completed"
    run.response_mode = mode
    run.agent_state = (
        {"billing_accepted_candidate": {"snapshot_id": str(uuid4())}} if mode == "edit" else {}
    )
    from yleum_api.models.message import Message

    db_session.add(Message(project_id=run.project_id, role="assistant", content="synthetic"))
    await db_session.flush()
    view = await credits.credits_view(db_session, user.id)
    expected = 0 if mode == "edit" else 1
    assert view["included_generations_remaining"] == expected
    assert view["included_edits_remaining"] == expected
    following, _project = await run_for(db_session, user)
    following.project_id = run.project_id
    assert await credits._initial(db_session, following) is (mode != "edit")
