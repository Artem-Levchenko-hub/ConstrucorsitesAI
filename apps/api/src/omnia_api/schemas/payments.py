from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PaymentCreate(BaseModel):
    package_code: Literal["start", "business", "pro"]
    idempotency_key: UUID


class PaymentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purpose: str
    subscription_id: UUID | None
    package_code: str
    amount_rub: Decimal
    credit_rub: Decimal
    status: str
    confirmation_url: str | None
    paid_at: datetime | None
    refunded_at: datetime | None
    created_at: datetime


class PaymentConfigPublic(BaseModel):
    enabled: bool
    packages: list[dict[str, str]]
    reason: str | None = None
