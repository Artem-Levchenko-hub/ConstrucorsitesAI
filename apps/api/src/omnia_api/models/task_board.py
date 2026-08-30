from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from omnia_api.models.base import Base


class TaskBoardTask(Base):
    __tablename__ = "task_board_tasks"

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="backlog")
    assignee: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attachments: Mapped[list[TaskBoardAttachment]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="TaskBoardAttachment.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('backlog', 'in_progress', 'review', 'done')",
            name="valid_status",
        ),
        CheckConstraint(
            "assignee IN ('alexey', 'alexey_jr', 'artem', 'roman')",
            name="valid_assignee",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="valid_priority",
        ),
        Index("ix_task_board_tasks_status_position", "status", "position"),
    )


class TaskBoardAttachment(Base):
    __tablename__ = "task_board_attachments"

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("task_board_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    task: Mapped[TaskBoardTask] = relationship(back_populates="attachments")

    __table_args__ = (
        CheckConstraint("size > 0", name="positive_size"),
        Index("ix_task_board_attachments_task_created", "task_id", "created_at"),
    )


class TaskBoardAttachmentCleanup(Base):
    """Durable outbox for object deletions committed after metadata removal."""

    __tablename__ = "task_board_attachment_cleanup"

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid4)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("size > 0", name="positive_size"),
        Index("ix_task_board_attachment_cleanup_created", "created_at"),
    )
