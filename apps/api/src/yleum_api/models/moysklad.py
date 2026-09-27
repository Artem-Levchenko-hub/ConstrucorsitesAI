"""Vendor installations are separate from Yleum account connections until paired."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class MoyskladInstallation(Base):
    __tablename__ = "moysklad_installations"

    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    account_name: Mapped[str] = mapped_column(Text, nullable=False)
    token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(Text, nullable=False)
    pairing_code_hash: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    vendor_activated: Mapped[bool] = mapped_column(nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MoyskladVendorReceipt(Base):
    __tablename__ = "moysklad_vendor_receipts"

    jti_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
