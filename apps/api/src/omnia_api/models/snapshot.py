import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from omnia_api.models.base import Base

if TYPE_CHECKING:
    from omnia_api.models.project import Project


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # SET NULL so a project's snapshots can be torn down row-by-row without
        # the timeline's self-reference (child.parent_id -> parent.id) raising a
        # ForeignKeyViolationError mid-cascade. Without this, deleting any
        # project with a multi-snapshot timeline 500s and the project is stuck
        # undeletable.
        ForeignKey("snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    preview_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_rollback_target: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(
        back_populates="snapshots",
        foreign_keys=[project_id],
    )

    __table_args__ = (
        CheckConstraint("char_length(commit_sha) = 40", name="ck_snapshots_commit_sha_len"),
        Index("ix_snapshots_project_id_created_at", "project_id", "created_at"),
    )
