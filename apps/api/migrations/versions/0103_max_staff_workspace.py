"""Separate MAX staff grants, work history and merchant checkout evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0103_max_staff_workspace"
down_revision = "0102_support_service"
branch_labels = None
depends_on = None


def _project(*, primary_key: bool = False) -> sa.Column:
    return sa.Column(
        "project_id",
        sa.Uuid(),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=primary_key,
    )


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def _time(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _work_state() -> list[sa.Column]:
    return [
        sa.Column("status", sa.Text(), nullable=False),
        _uuid("version"),
        _uuid("assignee_id", nullable=True),
        sa.Column("assignee_name", sa.Text()),
        sa.Column("outcome", sa.Text()),
    ]


def upgrade() -> None:
    op.create_table(
        "max_staff_settings",
        _project(primary_key=True),
        _uuid("owner_id"),
        _time("leads_enabled_at", nullable=True),
        _time("orders_enabled_at", nullable=True),
        sa.Column("orders_mode", sa.Text()),
        sa.Column("prep_minutes", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("prep_minutes BETWEEN 1 AND 120", name="prep_minutes"),
        sa.CheckConstraint(
            "(orders_mode IS NULL AND orders_enabled_at IS NULL) OR "
            "(orders_mode IS NOT NULL AND orders_mode IN ('pickup', 'yookassa') "
            "AND orders_enabled_at IS NOT NULL)",
            name="order_cutover",
        ),
    )
    op.create_table(
        "max_staff_members",
        _project(primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        _uuid("owner_id"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("permissions", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("permissions BETWEEN 1 AND 3", name="permissions"),
    )
    op.create_index("ix_max_staff_members_user_id", "max_staff_members", ["user_id"])
    op.create_table(
        "max_staff_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _project(),
        _uuid("owner_id"),
        sa.Column("digest", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("permissions", sa.SmallInteger(), nullable=False),
        _time("expires_at"),
        _uuid("accepted_user_id", nullable=True),
        sa.CheckConstraint("permissions BETWEEN 1 AND 3", name="permissions"),
    )
    op.create_index("ix_max_staff_invitations_project_id", "max_staff_invitations", ["project_id"])
    op.create_table(
        "max_staff_work",
        _project(primary_key=True),
        sa.Column("resource", sa.Text(), primary_key=True),
        sa.Column("record_id", sa.Uuid(), primary_key=True),
        _uuid("owner_id"),
        *_work_state(),
        sa.CheckConstraint("resource IN ('orders', 'leads')", name="resource"),
        sa.CheckConstraint("status IN ('new', 'in_progress', 'ready', 'completed')", name="status"),
    )
    op.create_table(
        "max_staff_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _project(),
        sa.Column("resource", sa.Text(), nullable=False),
        _uuid("record_id"),
        _uuid("owner_id"),
        _uuid("actor_id"),
        sa.Column("actor_name", sa.Text(), nullable=False),
        _uuid("operation_key"),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        *_work_state(),
        _time("created_at"),
        sa.UniqueConstraint(
            "project_id", "actor_id", "operation_key", name="uq_max_staff_audit_operation"
        ),
    )
    op.create_index(
        "ix_max_staff_audit_record",
        "max_staff_audit",
        ["project_id", "resource", "record_id", "created_at"],
    )
    op.create_table(
        "max_staff_payment_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _project(),
        _uuid("order_id"),
        _uuid("owner_id"),
        _uuid("binding_id"),
        _uuid("integration_id"),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.Text(), nullable=False),
        sa.Column("auth_mode", sa.Text(), nullable=False),
        sa.Column("grant_revision", sa.BigInteger(), nullable=False),
        sa.Column("oauth_client_key", sa.Text()),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="RUB"),
        sa.Column("request_body", JSONB(), nullable=False),
        sa.Column("provider_payment_id", sa.Text()),
        sa.Column("confirmation_url", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.Text()),
        sa.Column(
            "create_dispatch_claimed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("is_test", sa.Boolean()),
        _time("paid_at", nullable=True),
        _time("verified_at", nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _time("next_attempt_at", nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("amount_kopecks > 0", name=op.f("ck_max_staff_payment_attempts_amount")),
        sa.CheckConstraint("currency = 'RUB'", name=op.f("ck_max_staff_payment_attempts_currency")),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'canceled', 'needs_review')",
            name=op.f("ck_max_staff_payment_attempts_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_max_staff_payment_attempts_attempts")),
        sa.CheckConstraint(
            "failure_reason IS NULL OR failure_reason = 'receipt_required'",
            name=op.f("ck_max_staff_payment_attempts_failure_reason"),
        ),
        sa.UniqueConstraint("project_id", "order_id", name="uq_max_staff_payment_attempts_order"),
        sa.UniqueConstraint(
            "provider_payment_id", name="uq_max_staff_payment_attempts_provider_id"
        ),
    )
    op.create_index(
        "ix_max_staff_payment_attempts_due",
        "max_staff_payment_attempts",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    for table in (
        "max_staff_payment_attempts",
        "max_staff_audit",
        "max_staff_work",
        "max_staff_invitations",
        "max_staff_members",
        "max_staff_settings",
    ):
        op.drop_table(table)
