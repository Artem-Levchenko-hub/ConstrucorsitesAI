import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class Lead(Base):
    """A form submission captured from a project's PUBLIC site (``/p/<slug>``).

    The whole point of P-LEAD: a generated lead form must actually deliver
    somewhere instead of showing a fake «Спасибо» and discarding the data. The
    public site POSTs to ``/p/<slug>/lead`` (no auth); the row lands here and the
    owner reads it from the workspace «Заявки» inbox. ``data`` holds the raw
    submitted fields verbatim (whatever the form had — name/phone/message/…).
    """

    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Raw submitted form fields, exactly as the public form sent them.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Optional hint where it came from (form name / page path) for the owner.
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_leads_project_created", "project_id", "created_at"),)
