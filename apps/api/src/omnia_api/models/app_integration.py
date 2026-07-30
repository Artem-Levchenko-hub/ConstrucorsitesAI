"""Business-scoped third-party connections and per-project bindings."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class BusinessIntegration(Base):
    __tablename__ = "app_integrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    auth_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="credentials", default="credentials"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active", default="active"
    )
    public_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    credentials_enc: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    account_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'error')",
            name="ck_app_integrations_status_allowed",
        ),
        CheckConstraint(
            "auth_mode IN ('credentials', 'oauth', 'connector')",
            name="ck_app_integrations_auth_mode_allowed",
        ),
        UniqueConstraint(
            "business_id",
            "provider",
            name="uq_app_integrations_business_provider",
        ),
        Index("ix_app_integrations_business_id", "business_id"),
        Index("ix_app_integrations_created_by", "created_by_user_id"),
    )


class ProjectIntegrationBinding(Base):
    __tablename__ = "project_integration_bindings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="ready", default="ready"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'needs_setup', 'error')",
            name="ck_project_integration_bindings_status",
        ),
        UniqueConstraint(
            "project_id",
            "provider",
            name="uq_project_integration_bindings_project_provider",
        ),
        Index("ix_project_integration_bindings_integration", "integration_id"),
    )


class IntegrationOAuthState(Base):
    __tablename__ = "integration_oauth_states"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    state_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_integration_oauth_states_expires", "expires_at"),
    )


# Transitional import compatibility for internal code and third-party extensions.
AppIntegration = BusinessIntegration
