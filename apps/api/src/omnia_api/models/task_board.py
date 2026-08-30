from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

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
