"""Reserve creation/edit allowances before dispatch and settle accepted builds.

Free lifetime admission serializes on the owner, paid monthly admission on the
subscription. The caller commits the reservation with the run before dispatch.
Neither project deletion nor a new subscription resets lifetime usage.
"""

from datetime import UTC, datetime
from typing import Literal, TypedDict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.generation_allowance import GenerationAllowance
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.user import User
from yleum_api.services.entitlements import PlanContext, load_plan_context

GenerationKind = Literal["creation", "edit"]
AllowancePeriod = Literal["lifetime", "paid_month"]
PeriodBounds = tuple[datetime | None, datetime | None]
ENTITLEMENTS: dict[GenerationKind, str] = {
    "creation": "included_creations",
    "edit": "included_edits",
}


class AllowanceBucket(TypedDict):
    limit: int
    used: int
    in_progress: int
    remaining: int


class GenerationQuotaUsage(TypedDict):
    period: AllowancePeriod
    period_start: datetime | None
    period_end: datetime | None
    creations: AllowanceBucket
    edits: AllowanceBucket


def _terms(context: PlanContext) -> tuple[AllowancePeriod, dict[GenerationKind, int]] | None:
    plan = context.plan
    if plan is None:
        return None
    keys = (*ENTITLEMENTS.values(), "generation_allowance_period")
    if not any(key in plan.entitlements for key in keys):
        return None  # Historical contracts retain their original admission policy.
    period = plan.entitlements.get("generation_allowance_period")
    if period not in ("lifetime", "paid_month"):
        raise ApiError("subscription_entitlement_required", "Недоступные условия тарифа", 402)
    limits: dict[GenerationKind, int] = {}
    for kind, key in ENTITLEMENTS.items():
        value = plan.entitlements.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ApiError(
                "subscription_entitlement_required",
                "Недоступные условия тарифа",
                402,
                details={"entitlement": key, "period": period},
            )
        limits[kind] = max(0, value)
    return period, limits


def _period_bounds(
    context: PlanContext,
    period: AllowancePeriod,
    *,
    now: datetime,
) -> PeriodBounds | None:
    account, subscription, plan = context.account, context.subscription, context.plan
    if (
        account is None
        or subscription is None
        or plan is None
        or subscription.status != "active"
        or subscription.billing_account_id != account.id
        or subscription.user_id != account.personal_user_id
        or subscription.plan_id != plan.id
    ):
        return None
    if period == "lifetime":
        return (None, None) if plan.code == "free" and plan.price_rub == 0 else None
    start, end = subscription.current_period_start, subscription.current_period_end
    if (
        plan.price_rub <= 0
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
    kind: GenerationKind,
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
    if reservation.user_id != user_id or reservation.kind != kind or binding is None:
        raise ApiError("idempotency_conflict", "Generation allowance binding changed", 409)
    if reservation.status == "released":
        raise ApiError("conflict", "Generation allowance was already released", 409)
    return True


async def _usage_for_period(
    session: AsyncSession,
    context: PlanContext,
    *,
    period: AllowancePeriod,
    bounds: PeriodBounds,
    limits: dict[GenerationKind, int],
) -> GenerationQuotaUsage:
    """Count the validated period without taking a second admission-clock sample."""
    assert context.account is not None and context.subscription is not None
    statement = select(GenerationAllowance.kind, GenerationAllowance.status, func.count()).where(
        GenerationAllowance.billing_account_id == context.account.id,
        GenerationAllowance.user_id == context.account.personal_user_id,
        GenerationAllowance.status.in_(("reserved", "consumed")),
    )
    if period == "lifetime":
        statement = statement.where(
            GenerationAllowance.period_start.is_(None),
            GenerationAllowance.period_end.is_(None),
        )
    else:
        statement = statement.where(
            GenerationAllowance.subscription_id == context.subscription.id,
            GenerationAllowance.period_start == bounds[0],
            GenerationAllowance.period_end == bounds[1],
        )
    rows = await session.execute(
        statement.group_by(GenerationAllowance.kind, GenerationAllowance.status)
    )
    counts = {(kind, status): int(count) for kind, status, count in rows}
    carryover = 0
    if period == "lifetime":
        carryover = int(
            await session.scalar(
                select(User.free_creations_carryover).where(
                    User.id == context.account.personal_user_id
                )
            )
            or 0
        )

    def bucket(kind: GenerationKind) -> AllowanceBucket:
        used = counts.get((kind, "consumed"), 0) + (carryover if kind == "creation" else 0)
        in_progress = counts.get((kind, "reserved"), 0)
        return {
            "limit": limits[kind],
            "used": used,
            "in_progress": in_progress,
            "remaining": max(0, limits[kind] - used - in_progress),
        }

    return {
        "period": period,
        "period_start": bounds[0],
        "period_end": bounds[1],
        "creations": bucket("creation"),
        "edits": bucket("edit"),
    }


async def quota_usage(session: AsyncSession, context: PlanContext) -> GenerationQuotaUsage | None:
    terms = _terms(context)
    if terms is None:
        return None
    period, limits = terms
    bounds = _period_bounds(context, period, now=datetime.now(UTC))
    if bounds is None:
        return None
    return await _usage_for_period(session, context, period=period, bounds=bounds, limits=limits)


async def _reload_context(session: AsyncSession, user_id: UUID) -> PlanContext:
    context = await load_plan_context(session, user_id)
    if context.account is not None:
        await session.refresh(context.account)
    subscription = context.subscription
    if subscription is None:
        return context
    await session.refresh(subscription)
    plan = await session.get(BillingPlan, subscription.plan_id, populate_existing=True)
    return PlanContext(context.account, subscription, plan)


async def reserve_included_generation(
    session: AsyncSession,
    *,
    user_id: UUID,
    run_id: UUID,
    kind: GenerationKind,
) -> bool:
    if kind not in ("creation", "edit"):
        raise ApiError("validation_failed", "Неизвестный вид генерации", 400)
    existing = await session.get(GenerationAllowance, run_id, populate_existing=True)
    if existing is not None:
        return await _replay(session, existing, user_id, kind)
    context = await load_plan_context(session, user_id)
    terms = _terms(context)
    if terms is None:
        return False
    locked_period = terms[0]
    subscription_id = context.subscription.id if context.subscription else None
    if locked_period == "lifetime":
        owner = await session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if owner is None:
            raise ApiError("not_found", "user not found", 404)
    elif subscription_id is not None:
        # Both serializers retain compatibility with FK KEY SHARE locks.
        await session.execute(
            select(Subscription.id)
            .where(Subscription.id == subscription_id)
            .with_for_update(key_share=True)
        )
    existing = await session.get(GenerationAllowance, run_id, populate_existing=True)
    if existing is not None:
        return await _replay(session, existing, user_id, kind)
    context = await _reload_context(session, user_id)
    terms = _terms(context)
    if terms is None:
        raise ApiError("subscription_entitlement_required", "Требуется действующий тариф", 402)
    period, limits = terms
    if period != locked_period or (
        period == "paid_month"
        and context.subscription
        and context.subscription.id != subscription_id
    ):
        raise ApiError("conflict", "Тариф изменился, повторите запрос", 409)
    admitted_at = datetime.now(UTC)
    bounds = _period_bounds(context, period, now=admitted_at)
    if bounds is None:
        raise ApiError(
            "subscription_entitlement_required",
            "Требуется действующий тариф или оплаченный период",
            402,
            details={"entitlement": ENTITLEMENTS[kind], "period": period},
        )
    usage = await _usage_for_period(session, context, period=period, bounds=bounds, limits=limits)
    current = usage["creations"] if kind == "creation" else usage["edits"]
    if current["remaining"] == 0:
        raise ApiError(
            "entitlement_exceeded",
            "Включённые создания или правки закончились",
            402,
            details={
                **current,
                "period": period,
                "entitlement": ENTITLEMENTS[kind],
                "period_start": bounds[0].isoformat() if bounds[0] else None,
                "period_end": bounds[1].isoformat() if bounds[1] else None,
                "plan_code": context.plan.code if context.plan else None,
                "plan_version": context.plan.version if context.plan else None,
            },
        )
    assert context.account is not None and context.subscription is not None
    session.add(
        GenerationAllowance(
            run_id=run_id,
            billing_account_id=context.account.id,
            user_id=user_id,
            subscription_id=context.subscription.id,
            kind=kind,
            period_start=bounds[0],
            period_end=bounds[1],
            status="reserved",
        )
    )
    await session.flush()
    return True


async def settle_included_generation(
    session: AsyncSession,
    run: GenerationRun,
    *,
    accepted: bool,
) -> None:
    """Caller holds the run lock; persisted kind survives all response-mode choices."""
    if run.status not in {"completed", "failed", "cancelled"}:
        return
    reservation = await session.get(
        GenerationAllowance,
        run.id,
        with_for_update=True,
        populate_existing=True,
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
