"""One-time verified phone identity and durable SMS admission claims.

Revision ID: 0095_phone_verification
Revises: 0094_email_notifications
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0095_phone_verification"
down_revision = "0094_email_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone_e164", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_users_phone_e164", "users", ["phone_e164"])
    op.create_check_constraint(
        "phone_verified_identity", "users",
        "(phone_e164 IS NULL AND phone_verified_at IS NULL) OR "
        "(phone_e164 IS NOT NULL AND phone_e164 ~ '^\\+79[0-9]{9}$' AND phone_verified_at IS NOT NULL)",
    )
    op.create_table(
        "phone_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("ip_hash", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_status", sa.Text(), nullable=False),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="phone_attempts"),
        sa.CheckConstraint("delivery_status IN ('sending', 'sent', 'ambiguous', 'failed')", name="phone_delivery_status"),
    )
    for label, columns in (
        ("user_created", ["user_id", "created_at"]),
        ("phone_created", ["phone_e164", "created_at"]),
        ("ip_created", ["ip_hash", "created_at"]),
        ("created", ["created_at"]),
    ):
        op.create_index(f"ix_phone_challenges_{label}", "phone_challenges", columns)


def downgrade() -> None:
    op.drop_table("phone_challenges")
    op.drop_constraint("phone_verified_identity", "users", type_="check")
    op.drop_constraint("uq_users_phone_e164", "users", type_="unique")
    op.drop_column("users", "phone_verified_at")
    op.drop_column("users", "phone_e164")
