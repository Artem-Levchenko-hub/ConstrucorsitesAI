from datetime import datetime
from decimal import Decimal
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
    canceled_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    plan: BillingPlanPublic
