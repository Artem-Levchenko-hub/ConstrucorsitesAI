from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from yleum_api.core.config import get_settings
from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.billing import BillingPaymentMethod, BillingPlan, Subscription
from yleum_api.schemas.billing import (
    BillingPlanPublic,
    BillingUsagePublic,
    SubscriptionAction,
    SubscriptionPublic,
)
from yleum_api.services.billing_accounts import resolve_billing_account
from yleum_api.services.billing_usage import build_usage_report

router = APIRouter(prefix="/api/billing", tags=["billing"])

LIVE_SUBSCRIPTION_STATUSES = ("trialing", "active", "past_due", "paused")


async def _active_subscription(
    session: SessionDep,
    account_id: UUID,
    *,
    for_update: bool = False,
) -> tuple[Subscription, BillingPlan]:
    statement = (
        select(Subscription, BillingPlan)
        .join(BillingPlan, BillingPlan.id == Subscription.plan_id)
        .where(
            Subscription.billing_account_id == account_id,
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
        )
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise ApiError("not_found", "active subscription not found", 404)
    return row[0], row[1]


def _public_subscription(
    subscription: Subscription,
    plan: BillingPlan,
    *,
    can_restore: bool,
) -> SubscriptionPublic:
    return SubscriptionPublic(
        id=subscription.id,
        status=subscription.status,
        auto_renew=subscription.auto_renew,
        cancel_at_period_end=subscription.cancel_at_period_end,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        next_charge_at=subscription.next_charge_at,
        grace_period_ends_at=subscription.grace_period_ends_at,
        renewal_consent_version=subscription.renewal_consent_version,
        can_restore=can_restore,
        canceled_at=subscription.canceled_at,
        ended_at=subscription.ended_at,
        created_at=subscription.created_at,
        plan=BillingPlanPublic.model_validate(plan),
    )


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


@router.get("/usage", response_model=BillingUsagePublic)
async def get_usage(
    current_user: CurrentUserDep,
    session: SessionDep,
    period_start: Annotated[datetime | None, Query(alias="from")] = None,
    period_end: Annotated[datetime | None, Query(alias="to")] = None,
) -> BillingUsagePublic:
    """The account's spend journal: builds, app AI answers, publications, wallet
    movements and every plan entitlement next to its current use.

    Without `from`/`to` the window is the paid subscription period, or the
    current calendar month (UTC) on Free.
    """
    return await build_usage_report(
        session,
        current_user,
        period_start=period_start,
        period_end=period_end,
    )


@router.get("/subscription", response_model=SubscriptionPublic)
async def get_subscription(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> SubscriptionPublic:
    account = await resolve_billing_account(session, current_user.id)
    subscription, plan = await _active_subscription(session, account.id)
    can_restore = bool(
        subscription.cancel_at_period_end
        and subscription.payment_method_id is not None
        and subscription.current_period_end is not None
        and subscription.current_period_end > datetime.now(UTC)
    )
    return _public_subscription(subscription, plan, can_restore=can_restore)


@router.patch("/subscription", response_model=SubscriptionPublic)
async def manage_subscription(
    payload: SubscriptionAction,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> SubscriptionPublic:
    account = await resolve_billing_account(session, current_user.id)
    subscription, plan = await _active_subscription(
        session,
        account.id,
        for_update=True,
    )
    if plan.price_rub <= 0 or subscription.current_period_end is None:
        raise ApiError(
            "subscription_management_unavailable",
            "Бесплатный тариф не требует управления продлением",
            status.HTTP_409_CONFLICT,
        )
    now = datetime.now(UTC)
    if payload.action == "cancel":
        subscription.auto_renew = False
        subscription.cancel_at_period_end = True
        subscription.next_charge_at = None
        subscription.canceled_at = subscription.canceled_at or now
    else:
        if subscription.current_period_end <= now:
            raise ApiError(
                "subscription_management_unavailable",
                "Период подписки уже завершён",
                status.HTTP_409_CONFLICT,
            )
        settings = get_settings()
        if payload.consent_version != settings.legal_document_version:
            raise ApiError(
                "subscription_consent_required",
                "Подтвердите актуальные условия автопродления",
                status.HTTP_409_CONFLICT,
            )
        method = (
            await session.execute(
                select(BillingPaymentMethod)
                .where(
                    BillingPaymentMethod.id == subscription.payment_method_id,
                    BillingPaymentMethod.billing_account_id == account.id,
                    BillingPaymentMethod.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if method is None:
            raise ApiError(
                "subscription_management_unavailable",
                "Сохранённый способ оплаты недоступен",
                status.HTTP_409_CONFLICT,
            )
        method.consent_version = settings.legal_document_version
        method.consented_at = now
        subscription.auto_renew = True
        subscription.cancel_at_period_end = False
        subscription.next_charge_at = subscription.current_period_end
        subscription.renewal_consent_version = settings.legal_document_version
        subscription.renewal_consented_at = now
        subscription.canceled_at = None
    await session.commit()
    await session.refresh(subscription)
    return _public_subscription(
        subscription,
        plan,
        can_restore=bool(
            subscription.cancel_at_period_end
            and subscription.payment_method_id is not None
            and subscription.current_period_end > datetime.now(UTC)
        ),
    )
