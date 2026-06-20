import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from omnia_api.models.base import Base

if TYPE_CHECKING:
    from omnia_api.models.message import Message
    from omnia_api.models.snapshot import Snapshot


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default="ru", default="ru")
    design_preset_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lineage for the V4.1b "Remix this" fork: the project this one was
    # deep-copied from. NULL for organically-created projects. Self-FK with
    # ON DELETE SET NULL so deleting a source leaves its forks intact.
    forked_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Reified discovery answers (chip taps + free text) marshalled into a
    # FidelitySpec dict — the design the user steered the onboarding popup
    # toward. NULL = onboarding gave no assertable signal. Downstream gates read
    # this to check the live render against what was actually picked (V2.5).
    discovery_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Pre-computed onboarding question batch (owner rule 13 #1 — NORTH STAR pillar
    # 2). One upfront LLM pass plans all 3–4 product-tailored questions right after
    # the first prompt; they live here (list of {message, choices, allow_custom,
    # multi_select}) and are served one per turn with NO further gateway call —
    # zero wait between questions. NULL = batch path not used (or a zero-question
    # immediate build), so discovery falls back to per-question conversation.
    discovery_plan: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    image_gen_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    # V4.9 — the beauty floor's verdict on this project's SHARED surface. TRUE
    # once a composition gate scored a render of it as floor-green (taste +
    # hierarchy, plus first-paint when the stranger-cold path measures it). A
    # zero-signup fork inherits this from its source (perform_fork), so the
    # viral pool is transitively gated — a fork is re-shareable only if the app
    # it copied cleared the floor. Default FALSE: unscored ≠ vouched for.
    viral_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    current_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("snapshots.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    snapshots: Mapped[list["Snapshot"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        foreign_keys="Snapshot.project_id",
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="ck_projects_name_length"),
        # Keep in sync with migration 0017 + the `Template` literal in
        # schemas/project.py. Was stale at the 4 static V1 values; the DB has
        # allowed the container stacks since 0010 and the `code` template (any-
        # language, file-only, no container) since 0017.
        CheckConstraint(
            "template IN ('blank', 'landing', 'portfolio', 'blog', "
            "'fullstack', 'nextjs_entities', 'spa', 'tgbot', 'api', 'code')",
            name="ck_projects_template_allowed",
        ),
        Index("ix_projects_owner_id_created_at", "owner_id", "created_at"),
    )
