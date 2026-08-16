from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

BusinessKind = Literal["legal_entity", "sole_proprietor", "self_employed"]


class BusinessProfileCreate(BaseModel):
    kind: BusinessKind
    inn: str = Field(min_length=10, max_length=12)
    ogrn: str | None = Field(default=None, max_length=15)
    legal_name: str = Field(min_length=3, max_length=300)

    @field_validator("inn", "ogrn")
    @classmethod
    def digits_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = "".join(character for character in value if character.isdigit())
        if normalized != value.replace(" ", ""):
            raise ValueError("value must contain digits only")
        return normalized


class BusinessProfilePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: BusinessKind
    inn: str
    ogrn: str | None
    legal_name: str
    status: str
    verification_source: str | None
    verification_note: str | None
    verified_at: datetime | None
    created_at: datetime


class BusinessReviewPublic(BusinessProfilePublic):
    owner_email: str


class MaxAccessPublic(BaseModel):
    authenticated: bool = True
    email_verified: bool
    email_delivery_configured: bool
    business: BusinessProfilePublic | None = None
    can_create_project: bool
    reason: str | None = None
    legal_document_version: str
    payments_configured: bool


class BusinessDecision(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=500)
