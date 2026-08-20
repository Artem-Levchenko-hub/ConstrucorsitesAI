import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class GenerationRun(Base):
    """Durable identity and lifecycle for one accepted prompt execution.

    The partial unique index is the server-side single-flight guarantee: even
    different browser tabs or API processes cannot run two generations for the
    same project at once. ``idempotency_key`` makes a retried HTTP request return
    the original response instead of creating another user/assistant pair.
    """

    __tablename__ = "generation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending", default="pending"
    )
    response_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    # Exact user turn that created this run. Nullable for historical rows.
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Durable execution evidence. The DB column has existed since migration 0041;
    # exposing it in the ORM lets project-memory revisions cite exact changed files.
    agent_state: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'cancel_requested', "
            "'cancelled', 'completed', 'failed')",
            name="ck_generation_runs_status_allowed",
        ),
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_generation_runs_project_id_idempotency_key",
        ),
        Index(
            "uq_generation_runs_one_active_per_project",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running', 'cancel_requested')"),
        ),
        Index(
            "ix_generation_runs_project_id_created_at",
            "project_id",
            "created_at",
        ),
    )
