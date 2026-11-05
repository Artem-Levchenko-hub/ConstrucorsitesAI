from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class MaxStaffSettings(Base):
    __tablename__ = "max_staff_settings"
    __table_args__ = (
        CheckConstraint("prep_minutes BETWEEN 1 AND 120", name="prep_minutes"),
        CheckConstraint(
            "(orders_mode IS NULL AND orders_enabled_at IS NULL) OR "
            "(orders_mode IS NOT NULL AND orders_mode IN ('pickup', 'yookassa') "
            "AND orders_enabled_at IS NOT NULL)",
            name="order_cutover",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    owner_id: Mapped[UUID]
    leads_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    orders_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    orders_mode: Mapped[str | None] = mapped_column(Text)
    prep_minutes: Mapped[int] = mapped_column(SmallInteger)


class MaxStaffMember(Base):
    __tablename__ = "max_staff_members"
    __table_args__ = (
        CheckConstraint("permissions BETWEEN 1 AND 3", name="permissions"),
        Index("ix_max_staff_members_user_id", "user_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    owner_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(Text)
    permissions: Mapped[int] = mapped_column(SmallInteger)


class MaxStaffInvitation(Base):
    __tablename__ = "max_staff_invitations"
    __table_args__ = (
        CheckConstraint("permissions BETWEEN 1 AND 3", name="permissions"),
        Index("ix_max_staff_invitations_project_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    owner_id: Mapped[UUID]
    digest: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    permissions: Mapped[int] = mapped_column(SmallInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_user_id: Mapped[UUID | None]


class MaxStaffWork(Base):
    __tablename__ = "max_staff_work"
    __table_args__ = (
        CheckConstraint("resource IN ('orders', 'leads')", name="resource"),
        CheckConstraint("status IN ('new', 'in_progress', 'ready', 'completed')", name="status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    resource: Mapped[str] = mapped_column(Text, primary_key=True)
    record_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text)
    version: Mapped[UUID]
    assignee_id: Mapped[UUID | None]
    assignee_name: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)


class MaxStaffAudit(Base):
    __tablename__ = "max_staff_audit"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "actor_id", "operation_key", name="uq_max_staff_audit_operation"
        ),
        Index("ix_max_staff_audit_record", "project_id", "resource", "record_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    resource: Mapped[str] = mapped_column(Text)
    record_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    actor_name: Mapped[str] = mapped_column(Text)
    operation_key: Mapped[UUID]
    digest: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    version: Mapped[UUID]
    assignee_id: Mapped[UUID | None]
    assignee_name: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
