import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class Attestation(Base):
    """A saved, tamper-evident record of the runtime-gate verdicts for ONE build
    (fresh-plan Step 3 — "сохранённая аттестация" → deploy ↔ proven).

    Written best-effort AFTER the build's snapshot commits, in its own transaction,
    so a failed insert can never roll back the build. ``gates`` + ``digest`` come
    verbatim from ``services.attestation.build_attestation`` (sha256 over the
    canonical body — tamper-evident, not a PKI signature). ``overall_passed`` is the
    ship/no-ship summary a future deploy-gate reads. The table is created by
    ``Base.metadata.create_all()`` on startup (no migration needed).
    """

    __tablename__ = "attestations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SET NULL: a rolled-back/deleted snapshot must not erase the attestation record.
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    gates: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_attestations_project_created", "project_id", "created_at"),
    )
