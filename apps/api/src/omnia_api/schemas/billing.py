from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BillingPlanPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    version: int
    name: str
    price_rub: Decimal
    billing_interval: str
    included_credit_rub: Decimal
    entitlements: dict[str, object]


class SubscriptionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    auto_renew: bool
    cancel_at_period_end: bool
    current_period_start: datetime | None
    current_period_end: datetime | None
    next_charge_at: datetime | None
    grace_period_ends_at: datetime | None
    renewal_consent_version: str | None
    can_restore: bool
    canceled_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    plan: BillingPlanPublic


class SubscriptionAction(BaseModel):
    action: Literal["cancel", "restore"]
    consent_version: str | None = None


# --- GET /api/billing/usage — the account's spend journal for a period ------


class UsagePeriodPublic(BaseModel):
    start: datetime
    end: datetime
    # Where the default window came from: the paid subscription period, the
    # current calendar month for Free, or explicit `from`/`to` query params.
    source: Literal["subscription", "calendar_month", "custom"]


class UsageAIBucketPublic(BaseModel):
    """Gateway ledger rows of one kind: how many calls and what they cost."""

    calls: int = 0
    cost_rub: Decimal = Decimal("0")
    tokens_in: int = 0
    tokens_out: int = 0


class UsageGenerationsPublic(UsageAIBucketPublic):
    """Builds started in the period (generation_runs) with their model spend."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    active: int = 0


class UsagePublicationsPublic(BaseModel):
    total: int = 0
    # Distinct projects sent to the public runtime in the period.
    projects: int = 0


class UsageFreeGenerationsPublic(BaseModel):
    limit: int
    used: int
    left: int
    # UNLIMITED_GENERATIONS (testing escape hatch) makes every build free.
    unlimited: bool = False


class UsageWalletPublic(BaseModel):
    balance_rub: Decimal
    debited_rub: Decimal = Decimal("0")
    credited_rub: Decimal = Decimal("0")
    charges: int = 0


class EntitlementUsagePublic(BaseModel):
    key: str
    label: str
    kind: Literal["limit", "flag"]
    # `limit` null = no limit on this plan; only for kind == "limit".
    limit: int | None = None
    # Only for kind == "flag": whether the plan includes the feature.
    enabled: bool | None = None
    used: int
    exceeded: bool


class BillingUsagePublic(BaseModel):
    period: UsagePeriodPublic
    plan: BillingPlanPublic | None
    subscription_status: str | None
    generations: UsageGenerationsPublic
    app_ai_answers: UsageAIBucketPublic
    other_ai: UsageAIBucketPublic
    publications: UsagePublicationsPublic
    free_generations: UsageFreeGenerationsPublic
    wallet: UsageWalletPublic
    entitlements: list[EntitlementUsagePublic]
    total_ai_cost_rub: Decimal
