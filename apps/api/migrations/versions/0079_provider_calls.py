"""Retain provider attempts independently of customer billing; no backfill."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0079_provider_calls"
down_revision = "0078_usage_cost_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_scope", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("requested_model", sa.Text(), nullable=False),
        sa.Column("actual_model", sa.Text()),
        sa.Column("expense_owner", sa.Text(), nullable=False),
        *[
            sa.Column(name, postgresql.UUID(as_uuid=True))
            for name in ("user_id", "project_id", "run_id", "message_id", "usage_id")
        ],
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("free", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider_request_id", sa.Text()),
        *[
            sa.Column(name, sa.BigInteger())
            for name in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens")
        ],
        *[
            sa.Column(name, sa.Numeric(24, 8))
            for name in ("calculated_cost_rub", "provider_cost_rub", "provider_cost_usd")
        ],
        sa.Column("cost_provenance", postgresql.JSONB()),
        sa.Column("error_type", sa.Text()),
        sa.Column("receipt_hash", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("provider_scope = 'llmgw'", name="ck_provider_calls_scope"),
        sa.CheckConstraint(
            "status IN ('started','completed','failed','ambiguous')",
            name="ck_provider_calls_status",
        ),
        sa.CheckConstraint(
            "(expense_owner='user' AND user_id IS NOT NULL) OR "
            "(expense_owner='platform' AND user_id IS NULL)",
            name="ck_provider_calls_owner",
        ),
        sa.CheckConstraint(
            "(status='started' AND finished_at IS NULL) OR "
            "(status<>'started' AND finished_at IS NOT NULL)",
            name="ck_provider_calls_finished",
        ),
        *[
            sa.CheckConstraint(f"{name} IS NULL OR {name} >= 0", name=f"ck_provider_calls_{name}")
            for name in (
                "tokens_in",
                "tokens_out",
                "cache_read_tokens",
                "cache_write_tokens",
                "calculated_cost_rub",
                "provider_cost_rub",
                "provider_cost_usd",
            )
        ],
    )
    op.create_index(
        "uq_provider_calls_receipt",
        "provider_calls",
        ["provider_scope", "provider_request_id"],
        unique=True,
        postgresql_where=sa.text("provider_request_id IS NOT NULL"),
    )
    op.create_index("ix_provider_calls_created_at", "provider_calls", ["created_at"])
    op.create_index("ix_provider_calls_run_id", "provider_calls", ["run_id"])


def downgrade() -> None:
    op.drop_table("provider_calls")
