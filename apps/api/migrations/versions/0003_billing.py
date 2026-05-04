"""billing: usage + wallet_charges

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wallet_charges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("amount_rub", sa.Numeric(12, 4), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallet_charges"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_wallet_charges_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name="fk_wallet_charges_message_id_messages"
        ),
    )
    op.create_index(
        "ix_wallet_charges_user_id_created_at",
        "wallet_charges",
        ["user_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_rub", sa.Numeric(12, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_usage_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_usage_project_id_projects",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_usage_message_id_messages",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_usage_user_id_created_at",
        "usage",
        ["user_id", sa.text("created_at DESC")],
    )

    op.create_check_constraint(
        "ck_wallets_balance_non_negative",
        "wallets",
        "balance_rub >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_wallets_balance_non_negative", "wallets", type_="check")
    op.drop_table("usage")
    op.drop_table("wallet_charges")
