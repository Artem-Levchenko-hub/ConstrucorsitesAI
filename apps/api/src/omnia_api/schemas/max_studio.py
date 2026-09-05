"""Structured MAX Studio contract. Saving this data never invokes an LLM."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class MaxContentItem(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=600)
    price: str = Field(default="", max_length=80)
    action_label: str = Field(default="Открыть", min_length=1, max_length=40)
    active: bool = True


class MaxOperator(BaseModel):
    legal_name: str = Field(default="", max_length=200)
    inn: str = Field(default="", max_length=20)
    ogrn: str = Field(default="", max_length=20)
    address: str = Field(default="", max_length=300)


class MaxSupport(BaseModel):
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=40)
    response_time: str = Field(default="Ответим в течение 2 рабочих дней", max_length=120)


class MaxLegal(BaseModel):
    age_rating: Literal["0+", "6+", "12+", "16+", "18+"] = "0+"
    has_sales: bool = False
    has_user_content: bool = False
    marketing_notifications: bool = False
    personal_data_consent: bool = True
    terms_accepted: bool = False


class MaxProjectConfigPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    app_name: str = Field(min_length=1, max_length=100)
    app_type: Literal["loyalty", "catalog", "booking", "event", "education", "custom"]
    summary: str = Field(min_length=1, max_length=1000)
    audience: str = Field(default="", max_length=400)
    primary_action: str = Field(default="", max_length=200)
    features: list[str] = Field(default_factory=list, max_length=24)
    style: Literal["brand", "clean", "bright"] = "brand"
    brand_colors: str = Field(default="", max_length=200)
    content: list[MaxContentItem] = Field(default_factory=list, max_length=100)
    operator: MaxOperator = Field(default_factory=MaxOperator)
    support: MaxSupport = Field(default_factory=MaxSupport)
    legal: MaxLegal = Field(default_factory=MaxLegal)
    max_url_attached: bool = False

    @field_validator("features")
    @classmethod
    def unique_features(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for feature in value:
            clean = feature.strip()[:120]
            if clean and clean not in result:
                result.append(clean)
        return result


class MaxProjectConfigPublic(BaseModel):
    project_id: UUID
    config_version: int
    config: MaxProjectConfigPayload
    synced_snapshot_id: UUID | None = None
    updated_at: datetime | None = None
    application_mode: Literal["source", "runtime"] = "source"


class MaxUrlAttachedPayload(BaseModel):
    attached: bool


class MaxReadinessItem(BaseModel):
    id: str
    label: str
    done: bool
    blocking: bool = True
    action: str | None = None


class MaxReadinessPublic(BaseModel):
    ready_to_launch: bool
    progress: int
    items: list[MaxReadinessItem]


class MaxPreviewSessionUpstream(BaseModel):
    """Trusted shape returned by the internal orchestrator endpoint."""

    project_id: UUID
    bootstrap_url: str
    expires_at: datetime


class MaxPreviewSessionPublic(BaseModel):
    """One-time bootstrap URL for a MAX Mini App preview."""

    url: str
    expires_at: datetime


class MaxUsageStagePublic(BaseModel):
    id: str
    label: str
    cost_rub: float
    calls: int
    tokens_in: int
    tokens_out: int
    cache_read_tokens: int
    cache_write_tokens: int
    retries: int


class MaxUsagePublic(BaseModel):
    total_cost_rub: float
    run_cost_rub: float
    run_id: UUID | None = None
    run_status: str | None = None
    stages: list[MaxUsageStagePublic]
