from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from omnia_api.schemas.max_account import BusinessProfilePublic


class AdminUserPublic(BaseModel):
    id: UUID
    email: str
    role: Literal["user", "admin"]
    is_admin: bool
    unlimited_generations: bool
    status: str
    email_verified_at: datetime | None
    created_at: datetime
    last_login_at: datetime | None
    wallet_balance_rub: str
    business: BusinessProfilePublic | None = None


class AdminUserUpdate(BaseModel):
    role: Literal["user", "admin"] | None = None
    unlimited_generations: bool | None = None
    email_verified: bool | None = None
    status: Literal["active", "suspended"] | None = None
    business_verified: bool | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def at_least_one_change(self) -> "AdminUserUpdate":
        if (
            self.role is None
            and self.unlimited_generations is None
            and self.email_verified is None
            and self.status is None
            and self.business_verified is None
        ):
            raise ValueError("at least one account change is required")
        return self


class AdminAuditEventPublic(BaseModel):
    id: UUID
    actor_email: str
    target_email: str
    action: str
    details: dict[str, object]
    created_at: datetime
