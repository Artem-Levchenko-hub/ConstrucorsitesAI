"""First-response promise, separate receipts and durable narrow inbox deduplication."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0102_support_service"
down_revision = "0101_max_analytics_event_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("first_response_due_at", "first_responded_at"):
        op.add_column("support_tickets", sa.Column(name, sa.DateTime(timezone=True)))
    op.create_index(
        "ix_support_tickets_first_response",
        "support_tickets",
        ["first_response_due_at", "first_responded_at"],
    )
    op.add_column(
        "support_email_outbox",
        sa.Column("kind", sa.Text(), nullable=False, server_default="message"),
    )
    op.add_column("support_email_outbox", sa.Column("delivery_payload_encrypted", sa.Text()))
    op.drop_constraint("uq_support_email_recipient", "support_email_outbox", type_="unique")
    op.create_unique_constraint(
        "uq_support_email_recipient",
        "support_email_outbox",
        ["message_id", "recipient_email", "kind"],
    )
    op.create_check_constraint(
        "kind", "support_email_outbox", "kind IN ('message', 'acknowledgement')"
    )
    op.create_table(
        "support_inbox_cursors",
        sa.Column("mailbox_key", sa.Text(), primary_key=True),
        sa.Column("last_uid", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "support_inbound_receipts",
        sa.Column("delivery_key", sa.Text(), primary_key=True),
        sa.Column("message_key", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "ticket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("support_tickets.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM support_inbound_receipts) OR "
            "EXISTS (SELECT 1 FROM support_tickets WHERE first_response_due_at IS NOT NULL) OR "
            "EXISTS (SELECT 1 FROM support_email_outbox WHERE kind = 'acknowledgement')"
        )
    ):
        raise RuntimeError("support_service_evidence_present: retain promises and recovery data")
    op.drop_table("support_inbound_receipts")
    op.drop_table("support_inbox_cursors")
    op.drop_constraint(op.f("ck_support_email_outbox_kind"), "support_email_outbox", type_="check")
    op.drop_constraint("uq_support_email_recipient", "support_email_outbox", type_="unique")
    op.create_unique_constraint(
        "uq_support_email_recipient", "support_email_outbox", ["message_id", "recipient_email"]
    )
    op.drop_column("support_email_outbox", "delivery_payload_encrypted")
    op.drop_column("support_email_outbox", "kind")
    op.drop_index("ix_support_tickets_first_response", table_name="support_tickets")
    op.drop_column("support_tickets", "first_responded_at")
    op.drop_column("support_tickets", "first_response_due_at")
