from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChargePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    message_id: UUID | None = None
    amount_rub: Decimal
    description: str
    created_at: datetime


class WalletPublic(BaseModel):
    balance_rub: Decimal
    recent_charges: list[ChargePublic]


class TopupRequest(BaseModel):
    amount_rub: Decimal = Field(gt=0, le=Decimal("1000000"))


class TopupResponse(BaseModel):
    balance_rub: Decimal
