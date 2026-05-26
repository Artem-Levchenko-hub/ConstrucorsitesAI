"""SQLAlchemy 2 declarative models.

Conventions AI must follow:
- Every model inherits from `Base`.
- IDs are `UUID` from `uuid_pkg.uuid4` default.
- Timestamps `created_at` / `updated_at` set to `func.now()`.
- Foreign keys: `ondelete="CASCADE"` unless owner explicitly disagrees.
- Money columns: `Numeric(12, 4)` (matches Omnia wallet precision).

`User` is the auth-primitive — same role as the Next template's
`users` table. Don't rename or drop.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Single registry. All AI-added models inherit from this so
    `init_db()` picks them up on app startup."""


class User(Base):
    """End-user account. Used by `api/routers/auth.py` for the JWT flow.
    AI extends with profile fields (name, avatar, role, etc.) — don't
    rename id/email/password_hash."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Item(Base):
    """Example domain table — AI replaces with the user's real schema
    on the first prompt. Kept here so the OpenAPI docs aren't empty
    out of the box."""

    __tablename__ = "items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
