"""Safe capability requests made by a generated MAX Mini App."""

import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator
from pydantic.networks import validate_email


def _valid_phone(value: str | None) -> str | None:
    if not value:
        return None
    if not re.fullmatch(r"\+?[0-9 ()\-]{7,64}", value) or not 7 <= len(
        re.sub(r"\D", "", value)
    ) <= 15:
        raise ValueError("Введите корректный телефон")
    return value


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
        return {str(key)[:64]: str(item)[:512] for key, item in value.items() if str(key).strip()}


class RuntimePaymentPublic(BaseModel):
    id: str
    status: str
    confirmation_url: str | None = None


class RuntimePaymentStatusRequest(BaseModel):
    payment_id: str = Field(min_length=10, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class RuntimeLeadRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    comment: str | None = Field(default=None, max_length=4000)
    source: str = Field(default="MAX Mini App", max_length=128)

    @field_validator("name", "phone", "email", "comment", "source", mode="before")
    @classmethod
    def trim_fields(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        return _valid_phone(value)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str | None) -> str | None:
        return validate_email(value)[1] if value else None


class RuntimeLeadPublic(BaseModel):
    provider: str
    id: str
    details_status: str = "recorded"
    warning: str | None = None


class RuntimeLeadStatusRequest(BaseModel):
    lead_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


class RuntimeLeadStatusPublic(RuntimeLeadPublic):
    name: str
    pipeline_id: int
    pipeline_name: str
    status_id: int
    status_name: str
    updated_at: int
    checked_at: str


class RuntimeLeadListPublic(BaseModel):
    items: list[RuntimeLeadStatusPublic]
    has_more: bool = False


class RuntimeCatalogItem(BaseModel):
    id: str
    name: str
    description: str = ""
    price: float | None = None
    currency: str = "RUB"
    available: bool | None = None
    available_quantity: float | None = None
    image_url: str | None = None


class RuntimeCatalogPublic(BaseModel):
    provider: str
    items: list[RuntimeCatalogItem]


class RuntimeOrderLine(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, le=1_000, decimal_places=3)


class RuntimeOrderRequest(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=128)
    buyer_name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=64)
    lines: list[RuntimeOrderLine] = Field(min_length=1, max_length=50)

    @field_validator("buyer_name")
    @classmethod
    def valid_buyer_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Введите имя покупателя")
        return value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        # Preserve the original contact in the durable operation digest.
        _valid_phone(value.strip() if value is not None else None)
        return value


class RuntimeOrderSnapshotLine(BaseModel):
    product_id: UUID
    name: str | None
    quantity: str
    unit_price_minor: str
    total_minor: str


class RuntimeOrderSnapshot(BaseModel):
    # Prices fetched by the platform and included in the POST, not a receipt
    # of payment, fulfillment, or a subsequently changed provider document.
    amount_source: Literal["server_submitted"] = "server_submitted"
    lines: list[RuntimeOrderSnapshotLine]
    total_minor: str
    currency: str | None


class RuntimeOrderPublic(BaseModel):
    provider: str
    id: str
    snapshot: RuntimeOrderSnapshot | None = None
    provider_total_minor: str | None = None
    provider_total_currency: str | None = None


class RuntimeOrderStatusRequest(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=128)


class RuntimeOrderDetailsRequest(BaseModel):
    order_id: UUID


class RuntimeOrderReceiptPublic(BaseModel):
    provider: Literal["moysklad"] = "moysklad"
    idempotency_key: str
    status: Literal["dispatching", "succeeded", "rejected", "unknown"]
    created_at: datetime
    finished_at: datetime | None
    snapshot_status: Literal["recorded", "unavailable"]
    submitted_snapshot: RuntimeOrderSnapshot | None
    order: RuntimeOrderPublic | None


class RuntimeOrderListPublic(BaseModel):
    items: list[RuntimeOrderReceiptPublic]
    has_more: bool


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
