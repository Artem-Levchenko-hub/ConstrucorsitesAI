from fastapi import APIRouter
from sqlalchemy import select

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.billing import BillingPlan, Subscription
from omnia_api.schemas.billing import BillingPlanPublic, SubscriptionPublic

router = APIRouter(prefix="/api/billing", tags=["billing"])

LIVE_SUBSCRIPTION_STATUSES = ("trialing", "active", "past_due", "paused")


@router.get("/plans", response_model=list[BillingPlanPublic])
async def list_plans(session: SessionDep) -> list[BillingPlan]:
    return list(
        (
            await session.execute(
                select(BillingPlan)
                .where(BillingPlan.is_active.is_(True))
                .order_by(BillingPlan.sort_order, BillingPlan.code)
            )
        ).scalars()
    )


@router.get("/subscription", response_model=SubscriptionPublic)
async def get_subscription(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> SubscriptionPublic:
    row = (
        await session.execute(
            select(Subscription, BillingPlan)
            .join(BillingPlan, BillingPlan.id == Subscription.plan_id)
            .where(
                Subscription.user_id == current_user.id,
                Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
            )
        )
    ).one_or_none()
    if row is None:
        raise ApiError("not_found", "active subscription not found", 404)
    subscription, plan = row
    return SubscriptionPublic(
        id=subscription.id,
        status=subscription.status,
        auto_renew=subscription.auto_renew,
        cancel_at_period_end=subscription.cancel_at_period_end,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        next_charge_at=subscription.next_charge_at,
        grace_period_ends_at=subscription.grace_period_ends_at,
        canceled_at=subscription.canceled_at,
        ended_at=subscription.ended_at,
        created_at=subscription.created_at,
        plan=BillingPlanPublic.model_validate(plan),
    )
