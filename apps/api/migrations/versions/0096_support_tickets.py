"""Durable customer support, administrator audit and per-recipient mail delivery.

Revision ID: 0096_support_tickets
Revises: 0095_phone_verification
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0096_support_tickets"
down_revision = "0095_phone_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.BigInteger(), sa.Identity(), nullable=False, unique=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("guest_token_hash", sa.Text()),
        sa.Column("requester_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("create_request_key", sa.Text(), unique=True, nullable=False),
        sa.Column("create_payload_hash", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text()),
        sa.Column("assigned_admin_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('new', 'in_progress', 'done')", name="status"),
        sa.CheckConstraint("version > 0", name="version"),
        sa.CheckConstraint(
            "(owner_id IS NOT NULL AND guest_token_hash IS NULL) OR "
            "(owner_id IS NULL AND guest_token_hash IS NOT NULL)",
            name="owner",
        ),
        sa.CheckConstraint("(status = 'done') = (resolved_at IS NOT NULL)", name="resolved"),
    )
    for name, columns in (
        ("owner_updated", ["owner_id", "updated_at"]),
        ("status_updated", ["status", "updated_at"]),
        ("resolved", ["resolved_at"]),
    ):
        op.create_index(f"ix_support_tickets_{name}", "support_tickets", columns)
    op.create_table(
        "support_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_key", sa.Text(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.UniqueConstraint("ticket_id", "actor_key", "request_id", name="uq_support_message_request"),
        sa.CheckConstraint("author IN ('customer', 'support')", name="author"),
    )
    op.create_index("ix_support_messages_ticket_created", "support_messages", ["ticket_id", "created_at"])
    op.create_table(
        "support_email_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("support_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_email", sa.Text(), nullable=False),
        sa.Column("recipient_admin_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", "recipient_email", name="uq_support_email_recipient"),
        sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'uncertain', 'cancelled', 'failed')", name="status"),
    )
    op.create_index("ix_support_email_ready", "support_email_outbox", ["status", "next_attempt_at"])
    op.create_table(
        "support_rate_buckets",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("window", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_support_rate_window", "support_rate_buckets", ["window"])
    op.create_table(
        "support_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("old_status", sa.Text(), nullable=False),
        sa.Column("new_status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_support_audit_ticket", "support_audit", ["ticket_id", "created_at"])


def downgrade() -> None:
    # A schema rollback must not silently erase customer conversations or mail evidence.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM support_tickets)")):
        raise RuntimeError("support_evidence_present: retain support schema and deploy compatible code")
    for table in (
        "support_audit", "support_rate_buckets", "support_email_outbox",
        "support_messages", "support_tickets",
    ):
        op.drop_table(table)
