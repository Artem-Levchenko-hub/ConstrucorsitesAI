import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from omnia_api.models.base import Base

if TYPE_CHECKING:
    from omnia_api.models.wallet import Wallet


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable since V4.1a: anonymous (ephemeral) users created on
    # unauthenticated project creation have no credentials. The unique index on
    # email tolerates multiple NULLs; login/register only query non-null emails.
    email: Mapped[str | None] = mapped_column(CITEXT(), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True for ephemeral principals minted by the anon-project seam (V4.1a).
    # A `claim` re-points the project to a real account, leaving this row behind.
    is_anon: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # GitHub OAuth connection ("Push to GitHub"). Token is Fernet-encrypted at rest
    # (see core/crypto.py); all nullable until the user connects their account.
    github_login: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # First-N free generations (wow-effect onboarding). Incremented after each
    # successful free generation; once it reaches FREE_GENERATION_LIMIT
    # (core/config.py) the user is billed from their wallet like everyone else.
    free_generations_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    # Viral-funnel provenance (V4.2b return-edge). Set at registration from the
    # share-link return path: `signup_source` is a bounded enum ("share_link"
    # when a stranger came in via a /p/<slug> "Сделай свой" CTA, else NULL for
    # organic signups), and `referrer_project_id` is the SOURCE project the
    # visitor was looking at. Both NULL for a blank/organic signup — that NULL is
    # the falsifiable "the link, not a default, drives provenance" signal. A soft
    # analytics pointer with no FK: the referrer project may later be deleted
    # without orphaning this signup row's lineage record.
    signup_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    referrer_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    wallet: Mapped["Wallet"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
