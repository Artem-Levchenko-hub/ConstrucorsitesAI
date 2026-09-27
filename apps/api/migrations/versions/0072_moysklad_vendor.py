"""Installations and idempotent receipts for MoySklad Vendor API."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072_moysklad_vendor"
down_revision: str | None = "0071_oauth_login"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "moysklad_installations",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_name", sa.Text(), nullable=False),
        sa.Column("token_enc", sa.Text(), nullable=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("pairing_code_hash", sa.Text(), nullable=True, unique=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vendor_activated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "moysklad_vendor_receipts",
        sa.Column("jti_hash", sa.Text(), primary_key=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_moysklad_vendor_receipts_expires", "moysklad_vendor_receipts", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_moysklad_vendor_receipts_expires", table_name="moysklad_vendor_receipts")
    op.drop_table("moysklad_vendor_receipts")
    op.drop_table("moysklad_installations")
