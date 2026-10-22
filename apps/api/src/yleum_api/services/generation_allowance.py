"""Reserve paid monthly generations before dispatch and settle accepted builds.

The subscription row serializes admission across projects. The caller commits
the reservation together with the run before making any provider request.
Terminal settlement only changes a reservation once and never moves periods.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.generation_allowance import GenerationAllowance
from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.entitlements import PlanContext, load_plan_context


def _limit(context: PlanContext) -> int | None:
    plan = context.plan
    value = plan.entitlements.get("included_generations") if plan is not None else None
    return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else None


def _paid_period(context: PlanContext) -> tuple[datetime, datetime] | None:
    account, subscription, plan = context.account, context.subscription, context.plan
    if account is None or subscription is None or plan is None:
        return None
    start, end = subscription.current_period_start, subscription.current_period_end
    now = datetime.now(UTC)
    if (
        subscription.status != "active"
        or subscription.billing_account_id != account.id
        or subscription.user_id != account.personal_user_id
        or subscription.plan_id != plan.id
        or plan.price_rub <= 0
        or plan.billing_interval != "month"
        or start is None
        or end is None
        or start.tzinfo is None
        or end.tzinfo is None
        or not start <= now < end
    ):
        return None
    return start, end


async def _replay(
    session: AsyncSession,
    reservation: GenerationAllowance,
    user_id: UUID,
) -> bool:
    binding = await session.scalar(
        select(Subscription.id)
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(
            Subscription.id == reservation.subscription_id,
            Subscription.billing_account_id == reservation.billing_account_id,
            Subscription.user_id == user_id,
            BillingAccount.personal_user_id == user_id,
        )
    )
    if reservation.user_id != user_id or binding is None:
        raise ApiError("idempotency_conflict", "Generation allowance belongs to another user", 409)
    if reservation.status == "released":
        raise ApiError("conflict", "Generation allowance was already released", 409)
    return True


async def quota_usage(session: AsyncSession, context: PlanContext) -> dict[str, object] | None:
    limit = _limit(context)
    period = _paid_period(context)
    if limit is None or period is None:
        return None
    assert context.account is not None and context.subscription is not None
    rows = await session.execute(
        select(GenerationAllowance.status, func.count())
        .where(
            GenerationAllowance.billing_account_id == context.account.id,
            GenerationAllowance.subscription_id == context.subscription.id,
            GenerationAllowance.period_start == period[0],
            GenerationAllowance.period_end == period[1],
            GenerationAllowance.status.in_(("reserved", "consumed")),
        )
        .group_by(GenerationAllowance.status)
    )
    statuses = {status: count for status, count in rows}
    used, in_progress = int(statuses.get("consumed", 0)), int(statuses.get("reserved", 0))
    return {
        "limit": limit,
        "used": used,
        "in_progress": in_progress,
        "remaining": max(0, limit - used - in_progress),
        "period_start": period[0],
        "period_end": period[1],
    }


async def reserve_included_generation(
    session: AsyncSession, *, user_id: UUID, run_id: UUID
) -> bool:
    existing = await session.get(GenerationAllowance, run_id, populate_existing=True)
    if existing is not None:
        return await _replay(session, existing, user_id)
    context = await load_plan_context(session, user_id)
    if _limit(context) is None:
        return False
    subscription = context.subscription
    if subscription is not None:
        # NO KEY UPDATE lets parallel transactions retain their FK KEY SHARE
        # locks while admission waits; FOR UPDATE can deadlock those inserts.
        subscription = await session.scalar(
            select(Subscription)
            .where(Subscription.id == subscription.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        plan = await session.get(BillingPlan, subscription.plan_id) if subscription else None
        context = PlanContext(context.account, subscription, plan)
    period = _paid_period(context)
    if period is None:
        raise ApiError(
            "subscription_entitlement_required",
            "Требуется действующий оплаченный период",
            402,
            details={"entitlement": "included_generations"},
        )
    # Another admission of the same run can have committed while we waited.
    existing = await session.get(GenerationAllowance, run_id, populate_existing=True)
    if existing is not None:
        return await _replay(session, existing, user_id)
    usage = await quota_usage(session, context)
    assert usage is not None and context.account is not None and subscription is not None
    if usage["remaining"] == 0:
        raise ApiError(
            "entitlement_exceeded",
            "Включённые генерации за оплаченный месяц закончились",
            402,
            details={
                **usage,
                "period_start": period[0].isoformat(),
                "period_end": period[1].isoformat(),
                "entitlement": "included_generations",
                "plan_code": context.plan.code if context.plan else None,
                "plan_version": context.plan.version if context.plan else None,
            },
        )
    session.add(
        GenerationAllowance(
            run_id=run_id,
            billing_account_id=context.account.id,
            user_id=user_id,
            subscription_id=subscription.id,
            period_start=period[0],
            period_end=period[1],
            status="reserved",
        )
    )
    await session.flush()
    return True


async def settle_included_generation(
    session: AsyncSession, run: GenerationRun, *, accepted: bool
) -> None:
    """Caller holds the run lock and supplies its validated accepted-build verdict."""
    if run.status not in {"completed", "failed", "cancelled"}:
        return
    reservation = await session.get(
        GenerationAllowance, run.id, with_for_update=True, populate_existing=True
    )
    if reservation is None or reservation.status != "reserved":
        return
    if reservation.user_id != run.user_id:
        raise ValueError("Generation allowance owner changed")
    if accepted and (run.status != "completed" or run.response_mode not in {"build", "edit"}):
        raise ValueError("Only an accepted completed build can consume its allowance")
    reservation.status = "consumed" if accepted else "released"
    reservation.settled_at = datetime.now(UTC)
    await session.flush()
