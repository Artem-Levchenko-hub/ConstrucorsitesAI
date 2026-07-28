from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

HeroMediaPlanKind = Literal["static", "product-demo", "motion", "video", "cinematic"]
HeroMediaFocusPreference = Literal["auto", "product", "interface", "atmosphere", "result"]
HeroMediaMotionPreference = Literal["auto", "calm", "lively", "cinematic"]
HeroMediaAssetRole = Literal["source", "poster", "clip"]
HeroMediaAssetKind = Literal["image", "video"]


class HeroMediaAssetPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    owner_id: UUID
    asset_role: HeroMediaAssetRole
    media_kind: HeroMediaAssetKind
    storage_url: str
    storage_key: str | None = None
    mime_type: str
    original_filename: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    bytes_size: int | None = None
    consent_confirmed: bool
    moderation_status: str
    details: dict[str, Any] | None = None
    created_at: datetime


class HeroMediaShot(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=240)
    primary_asset_index: int | None = Field(default=None, ge=0, le=5)
    secondary_asset_index: int | None = Field(default=None, ge=0, le=5)
    use_source_as_poster: bool = False
    still_prompt: str | None = Field(default=None, max_length=1200)
    motion_prompt: str | None = Field(default=None, max_length=1200)
    first_frame_prompt: str | None = Field(default=None, max_length=1200)
    last_frame_prompt: str | None = Field(default=None, max_length=1200)


class HeroMediaDecision(BaseModel):
    plan_kind: HeroMediaPlanKind
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1, max_length=600)
    recommended_focus: str = Field(min_length=1, max_length=80)
    recommended_tone: str = Field(min_length=1, max_length=80)
    brand_fit_note: str = Field(min_length=1, max_length=240)
    performance_note: str = Field(min_length=1, max_length=240)
    accessibility_note: str = Field(min_length=1, max_length=240)
    requires_confirmation: bool = True
    hero_headline: str = Field(min_length=1, max_length=180)
    hero_subheadline: str = Field(min_length=1, max_length=300)
    primary_cta_label: str = Field(min_length=1, max_length=80)
    visual_style: str = Field(min_length=1, max_length=240)
    storyboard: list[HeroMediaShot] = Field(default_factory=list, max_length=3)


class HeroMediaPlanCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    business_type: str | None = Field(default=None, max_length=120)
    style_preference: str | None = Field(default=None, max_length=120)
    focus_preference: HeroMediaFocusPreference = "auto"
    motion_preference: HeroMediaMotionPreference = "auto"
    asset_ids: list[UUID] = Field(default_factory=list, max_length=6)


class HeroMediaPlanApproveRequest(BaseModel):
    selected_plan_kind: HeroMediaPlanKind


class HeroMediaPlanPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    owner_id: UUID
    status: Literal["draft", "approved", "rejected"]
    input_prompt: str
    business_type: str | None = None
    style_preference: str | None = None
    focus_preference: str | None = None
    motion_preference: str | None = None
    asset_ids: list[UUID]
    recommended_plan_kind: HeroMediaPlanKind
    selected_plan_kind: HeroMediaPlanKind | None = None
    plan: HeroMediaDecision
    created_at: datetime
    updated_at: datetime


class HeroMediaBundlePublic(BaseModel):
    mode: HeroMediaPlanKind
    poster_url: str
    video_url: str | None = None
    headline: str
    subheadline: str
    primary_cta_label: str
    explanation: str
    html: str
    css: str
    js: str


class HeroMediaProgressEvent(BaseModel):
    at: datetime
    status: str
    detail: str


class HeroMediaRenderCreateRequest(BaseModel):
    plan_id: UUID


class HeroMediaRenderPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    owner_id: UUID
    brief_id: UUID
    status: Literal["queued", "rendering", "assembling", "completed", "failed"]
    media_plan: HeroMediaPlanKind
    status_detail: str | None = None
    provider_summary: str | None = None
    poster_asset_id: UUID | None = None
    video_asset_id: UUID | None = None
    applied_snapshot_id: UUID | None = None
    bundle: HeroMediaBundlePublic | None = None
    progress_log: list[HeroMediaProgressEvent] = Field(default_factory=list)
    error: str | None = None
    retry_count: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    applied_at: datetime | None = None
