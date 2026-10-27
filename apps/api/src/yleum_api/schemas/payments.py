from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PaymentCreate(BaseModel):
    package_code: Literal["start", "business", "pro"]
    idempotency_key: UUID


class SubscriptionCheckoutCreate(BaseModel):
    plan_code: Literal["pro", "business"]
    idempotency_key: UUID
    auto_renew: bool = False
    consent_version: str | None = None


class PaymentTestCheckoutCreate(BaseModel):
    offer_id: UUID
    idempotency_key: UUID


class PaymentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purpose: str
    subscription_id: UUID | None
    package_code: str
    amount_rub: Decimal
    credit_rub: Decimal
    edit_units: int = 0
    edits_only: bool = False
    package_version: int | None = None
    status: str
    confirmation_url: str | None
    paid_at: datetime | None
    refunded_at: datetime | None
    created_at: datetime


class PaymentPackagePublic(BaseModel):
    code: str
    price_rub: Decimal
    credit_rub: Decimal
    title: str
    edit_units: int
    edits_only: bool
    package_version: int


class PaymentConfigPublic(BaseModel):
    enabled: bool
    packages: list[PaymentPackagePublic]
    reason: str | None = None
