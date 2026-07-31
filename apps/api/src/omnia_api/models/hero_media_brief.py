from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class HeroMediaBrief(Base):
    __tablename__ = "hero_media_briefs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="draft",
        default="draft",
    )
    input_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    business_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    style_preference: Mapped[str | None] = mapped_column(Text, nullable=True)
    focus_preference: Mapped[str | None] = mapped_column(Text, nullable=True)
    motion_preference: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    recommended_plan_kind: Mapped[str] = mapped_column(Text, nullable=False)
    selected_plan_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="status_allowed",
        ),
        CheckConstraint(
            "recommended_plan_kind IN ('static', 'product-demo', 'motion', 'video', 'cinematic')",
            name="recommended_plan_kind_allowed",
        ),
        CheckConstraint(
            "selected_plan_kind IS NULL OR selected_plan_kind IN "
            "('static', 'product-demo', 'motion', 'video', 'cinematic')",
            name="selected_plan_kind_allowed",
        ),
    )
