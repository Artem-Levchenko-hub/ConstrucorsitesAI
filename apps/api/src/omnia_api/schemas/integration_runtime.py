"""Safe capability requests made by a generated MAX Mini App."""

import json
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


class RuntimeIntegrationStatus(BaseModel):
    providers: list[str]
    capabilities: list[str]
    analytics_counter_id: str | None = None


class RuntimePaymentRequest(BaseModel):
    amount: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)
    description: str = Field(min_length=1, max_length=128)
    return_url: HttpUrl
    idempotency_key: str = Field(min_length=16, max_length=128)
    metadata: dict[str, str] = Field(default_factory=dict)
    receipt: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 16:
            raise ValueError("too many metadata values")
        return {
            str(key)[:64]: str(item)[:512]
            for key, item in value.items()
            if str(key).strip()
        }


class RuntimePaymentPublic(BaseModel):
    id: str
    status: str
    confirmation_url: str | None = None


class RuntimePaymentStatusRequest(BaseModel):
    payment_id: str = Field(min_length=10, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class RuntimeLeadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    comment: str | None = Field(default=None, max_length=4000)
    source: str = Field(default="MAX Mini App", max_length=128)


class RuntimeLeadPublic(BaseModel):
    provider: str
    id: str


class RuntimeCatalogItem(BaseModel):
    id: str
    name: str
    description: str = ""
    price: float | None = None
    currency: str = "RUB"
    available: bool = True
    image_url: str | None = None


class RuntimeCatalogPublic(BaseModel):
    provider: str
    items: list[RuntimeCatalogItem]


class RuntimeAIRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    instructions: str = Field(default="", max_length=2_000)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def validate_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 40:
            raise ValueError("too many context fields")
        if len(json.dumps(value, ensure_ascii=False, default=str)) > 16_384:
            raise ValueError("context is too large")
        return value


class RuntimeAIPublic(BaseModel):
    answer: str
    model: str
