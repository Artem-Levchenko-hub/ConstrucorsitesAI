"""Structured Yleum contract. Saving this data never invokes an LLM."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class MaxContentItem(BaseModel):
    """One catalog position: a product, service, session or lesson.

    Only ``title`` is required. The rest describes the position the way a real
    storefront does — section, photo, price, availability and the variants a
    buyer picks (sizes, volumes, durations) — so the generated screens have
    something to render besides a line of text. Every field added after the
    first release carries a default: configurations saved earlier stay valid.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=600)
    price: str = Field(default="", max_length=80)
    availability: Literal["in_stock", "on_request", "out_of_stock"] = "in_stock"
    options: list[str] = Field(default_factory=list, max_length=24)
    image_url: str = Field(default="", max_length=500)
    action_label: str = Field(default="Открыть", min_length=1, max_length=40)
    active: bool = True

    @field_validator("options")
    @classmethod
    def clean_options(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for option in value:
            clean = option.strip()[:40]
            if clean and clean not in result:
                result.append(clean)
        return result

    @field_validator("image_url")
    @classmethod
    def https_image_url(cls, value: str) -> str:
        clean = value.strip()
        if clean and not clean.startswith("https://"):
            raise ValueError("image_url must be an https:// address")
        return clean


class MaxOperator(BaseModel):
    # Only a display name for the app's own documents: a generated policy or
    # terms page has to say who runs the app. Never requisites — ИНН, ОГРН,
    # address or phone are not collected anywhere (152-ФЗ, ст. 5). Stored
    # configurations from before this rule may still carry those keys; they are
    # ignored on read and dropped on the next save.
    legal_name: str = Field(default="", max_length=200)


class MaxSupport(BaseModel):
    email: EmailStr | None = None
    response_time: str = Field(default="Ответим в течение 2 рабочих дней", max_length=120)


class MaxLegal(BaseModel):
    age_rating: Literal["0+", "6+", "12+", "16+", "18+"] = "0+"
    has_sales: bool = False
    has_user_content: bool = False
    marketing_notifications: bool = False
    personal_data_consent: bool = True
    terms_accepted: bool = False
    # The owner's own privacy policy, when the company already has one: the
    # generated privacy page then points to it instead of describing the
    # processing itself, and Studio needs no operator details at all.
    policy_url: str = Field(default="", max_length=300)

    @field_validator("policy_url")
    @classmethod
    def https_policy_url(cls, value: str) -> str:
        clean = value.strip()
        if clean and not clean.startswith("https://"):
            raise ValueError("policy_url must be an https:// address")
        return clean


class MaxProjectConfigPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    app_name: str = Field(min_length=1, max_length=100)
    app_type: Literal["loyalty", "catalog", "booking", "event", "education", "custom"]
    summary: str = Field(min_length=1, max_length=20_000)
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

    @classmethod
    def default_for(cls, app_name: str) -> "MaxProjectConfigPayload":
        """What a project has before its owner saved anything: a name and a
        neutral summary. Publishing needs no requisites — only the owner's
        confirmation of the app's documents (`legal.terms_accepted`)."""
        return cls(
            app_name=app_name,
            app_type="custom",
            summary="Мини-приложение для пользователей MAX",
        )

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


class MaxContentImagePublic(BaseModel):
    """Public URL of an uploaded catalog photo, ready to store in an item."""

    url: str


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
