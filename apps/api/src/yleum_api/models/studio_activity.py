from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class StudioActivity(Base):
    __tablename__ = "studio_activity"

    actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    release: Mapped[str] = mapped_column(String(40), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (Index("ix_studio_activity_minute", "minute"),)
