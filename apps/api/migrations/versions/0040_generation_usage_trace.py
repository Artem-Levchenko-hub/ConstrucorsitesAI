"""trace native generation usage by run and stage

Revision ID: 0040_generation_usage_trace
Revises: 0039_attestation_integrity
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0040_generation_usage_trace"
down_revision: str | None = "0039_attestation_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage", sa.Column("run_id", UUID(as_uuid=True), nullable=True))
    op.add_column("usage", sa.Column("stage", sa.Text(), nullable=True))
    op.add_column(
        "usage",
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "usage",
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "usage",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("usage", sa.Column("provider_request_id", sa.Text(), nullable=True))
    op.add_column(
        "usage",
        sa.Column("provider_cost_usd", sa.Numeric(18, 8), nullable=True),
    )
    op.create_foreign_key(
        "fk_usage_run_id_generation_runs",
        "usage",
        "generation_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_usage_run_id_created_at", "usage", ["run_id", "created_at"])
    op.create_index("ix_usage_project_id_created_at", "usage", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_project_id_created_at", table_name="usage")
    op.drop_index("ix_usage_run_id_created_at", table_name="usage")
    op.drop_constraint("fk_usage_run_id_generation_runs", "usage", type_="foreignkey")
    op.drop_column("usage", "provider_cost_usd")
    op.drop_column("usage", "provider_request_id")
    op.drop_column("usage", "retry_count")
    op.drop_column("usage", "cache_write_tokens")
    op.drop_column("usage", "cache_read_tokens")
    op.drop_column("usage", "stage")
    op.drop_column("usage", "run_id")
