"""Read model behind ``GET /api/billing/usage``.

Nothing here is a second copy of the truth. Each figure is aggregated from the
table that already records the event:

* generations       -> ``generation_runs`` (count by status) + ``usage`` rows
                       that carry a ``run_id`` (the model spend of those builds);
* app AI answers    -> ``usage`` rows with ``stage = 'runtime_ai'`` (visitors of
                       a published MAX app asking its built-in AI);
* other AI          -> the remaining ``usage`` rows (discovery questions, advice,
                       transcription, ...);
* publications      -> ``billing_usage_events`` (kind ``publication``);
* wallet movements  -> ``wallet_charges``;
* entitlements      -> the live plan next to live counts (services.entitlements).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import FREE_GENERATION_LIMIT, get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.billing_usage_event import BillingUsageEvent
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.usage import Usage
from yleum_api.models.user import User
from yleum_api.models.wallet import Wallet
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.schemas.billing import (
    BillingPlanPublic,
    BillingUsagePublic,
    EntitlementUsagePublic,
    UsageAIBucketPublic,
    UsageFreeGenerationsPublic,
    UsageGenerationsPublic,
    UsagePeriodPublic,
    UsagePublicationsPublic,
    UsageWalletPublic,
)
from yleum_api.services import entitlements

# The gateway stamps this stage on every answer a published app's visitor gets
# (apps/api routers/integration_runtime.py sends it as metadata.stage).
RUNTIME_AI_STAGE = "runtime_ai"
MAX_PERIOD_DAYS = 366
ACTIVE_RUN_STATUSES = ("pending", "queued_for_capacity", "running", "cancel_requested")


def resolve_period(
    *,
    now: datetime,
    period_start: datetime | None,
    period_end: datetime | None,
    subscription_period_start: datetime | None,
) -> UsagePeriodPublic:
    """Explicit bounds win; otherwise the paid period, otherwise this month."""
    if period_start is not None or period_end is not None:
        start = _aware(period_start) if period_start is not None else None
        end = _aware(period_end) if period_end is not None else now
        if start is None:
            start = end - timedelta(days=30)
        if start >= end:
            raise ApiError("validation_failed", "period start must be before its end", 422)
        if end - start > timedelta(days=MAX_PERIOD_DAYS):
            raise ApiError(
                "validation_failed", f"period may not exceed {MAX_PERIOD_DAYS} days", 422
            )
        return UsagePeriodPublic(start=start, end=end, source="custom")
    if subscription_period_start is not None:
        return UsagePeriodPublic(
            start=_aware(subscription_period_start), end=now, source="subscription"
        )
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return UsagePeriodPublic(start=month_start, end=now, source="calendar_month")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def classify_usage_row(
    run_id: object, stage: str | None
) -> Literal["generation", "app_ai", "other"]:
    """Bucket of one gateway ledger row (pure, unit-tested)."""
    if run_id is not None:
        return "generation"
    if stage == RUNTIME_AI_STAGE:
        return "app_ai"
    return "other"


async def build_usage_report(
    session: AsyncSession,
    user: User,
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    now: datetime | None = None,
) -> BillingUsagePublic:
    current = now or datetime.now(UTC)
    context = await entitlements.load_plan_context(session, user.id)
    period = resolve_period(
        now=current,
        period_start=period_start,
        period_end=period_end,
        subscription_period_start=(
            context.subscription.current_period_start if context.subscription is not None else None
        ),
    )
    start, end = period.start, period.end

    # Builds started in the period, by outcome.
    run_rows = (
        await session.execute(
            select(GenerationRun.status, func.count(GenerationRun.id))
            .where(
                GenerationRun.user_id == user.id,
                GenerationRun.created_at >= start,
                GenerationRun.created_at < end,
            )
            .group_by(GenerationRun.status)
        )
    ).all()
    by_status = {str(status): int(count) for status, count in run_rows}
    generations = UsageGenerationsPublic(
        total=sum(by_status.values()),
        completed=by_status.get("completed", 0),
        failed=by_status.get("failed", 0),
        cancelled=by_status.get("cancelled", 0),
        active=sum(by_status.get(status, 0) for status in ACTIVE_RUN_STATUSES),
    )

    # Gateway ledger rows split into the three buckets, in one query.
    bucket = case(
        (Usage.run_id.is_not(None), "generation"),
        (Usage.stage == RUNTIME_AI_STAGE, "app_ai"),
        else_="other",
    )
    usage_rows = (
        await session.execute(
            select(
                bucket.label("bucket"),
                func.count(Usage.id),
                func.coalesce(func.sum(Usage.cost_rub), 0),
                func.coalesce(func.sum(Usage.tokens_in), 0),
                func.coalesce(func.sum(Usage.tokens_out), 0),
            )
            .where(
                Usage.user_id == user.id,
                Usage.created_at >= start,
                Usage.created_at < end,
            )
            .group_by(bucket)
        )
    ).all()
    buckets: dict[str, UsageAIBucketPublic] = {}
    for name, calls, cost, tokens_in, tokens_out in usage_rows:
        buckets[str(name)] = UsageAIBucketPublic(
            calls=int(calls),
            cost_rub=Decimal(str(cost)),
            tokens_in=int(tokens_in),
            tokens_out=int(tokens_out),
        )
    generation_spend = buckets.get("generation", UsageAIBucketPublic())
    generations = generations.model_copy(
        update={
            "calls": generation_spend.calls,
            "cost_rub": generation_spend.cost_rub,
            "tokens_in": generation_spend.tokens_in,
            "tokens_out": generation_spend.tokens_out,
        }
    )
    app_ai = buckets.get("app_ai", UsageAIBucketPublic())
    other_ai = buckets.get("other", UsageAIBucketPublic())

    # Publications journaled for the account in the period.
    publications = UsagePublicationsPublic()
    wallet_public = UsageWalletPublic(balance_rub=Decimal("0"))
    if context.account is not None:
        publication_row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(BillingUsageEvent.quantity), 0),
                    func.count(func.distinct(BillingUsageEvent.project_id)),
                ).where(
                    BillingUsageEvent.billing_account_id == context.account.id,
                    BillingUsageEvent.kind == "publication",
                    BillingUsageEvent.created_at >= start,
                    BillingUsageEvent.created_at < end,
                )
            )
        ).one()
        publications = UsagePublicationsPublic(
            total=int(publication_row[0]), projects=int(publication_row[1])
        )
        balance = await session.scalar(
            select(Wallet.balance_rub).where(Wallet.billing_account_id == context.account.id)
        )
        charge_row = (
            await session.execute(
                select(
                    func.count(WalletCharge.id),
                    func.coalesce(
                        func.sum(
                            case(
                                (WalletCharge.amount_rub < 0, -WalletCharge.amount_rub),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (WalletCharge.amount_rub > 0, WalletCharge.amount_rub),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                ).where(
                    WalletCharge.billing_account_id == context.account.id,
                    WalletCharge.created_at >= start,
                    WalletCharge.created_at < end,
                )
            )
        ).one()
        wallet_public = UsageWalletPublic(
            balance_rub=Decimal(str(balance)) if balance is not None else Decimal("0"),
            debited_rub=Decimal(str(charge_row[1])),
            credited_rub=Decimal(str(charge_row[2])),
            charges=int(charge_row[0]),
        )

    used_free = int(user.free_generations_used or 0)
    unlimited = bool(get_settings().unlimited_generations)
    free_generations = UsageFreeGenerationsPublic(
        limit=FREE_GENERATION_LIMIT,
        used=used_free,
        left=max(0, FREE_GENERATION_LIMIT - used_free),
        unlimited=unlimited,
    )

    usages = await entitlements.entitlement_usages(session, user.id, context=context)
    return BillingUsagePublic(
        period=period,
        plan=BillingPlanPublic.model_validate(context.plan) if context.plan else None,
        subscription_status=(
            context.subscription.status if context.subscription is not None else None
        ),
        generations=generations,
        app_ai_answers=app_ai,
        other_ai=other_ai,
        publications=publications,
        free_generations=free_generations,
        wallet=wallet_public,
        entitlements=[
            EntitlementUsagePublic(
                key=item.key,
                label=item.label,
                kind=item.kind,
                limit=item.limit,
                enabled=item.enabled,
                used=item.used,
                exceeded=item.exceeded,
            )
            for item in usages
        ],
        total_ai_cost_rub=generations.cost_rub + app_ai.cost_rub + other_ai.cost_rub,
    )
