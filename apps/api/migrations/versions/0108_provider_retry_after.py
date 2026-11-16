"""Retain the cooldown from a committed LLMGW rate-limit refusal."""

import sqlalchemy as sa
from alembic import op

revision = "0108_provider_retry_after"
down_revision = "0107_expired_provider_reservation"
branch_labels = None
depends_on = None

_TABLE = "provider_calls"
_CHECK = "ck_provider_calls_retry_after"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("retry_after_seconds", sa.Integer()))
    op.create_check_constraint(
        op.f(_CHECK), _TABLE,
        "retry_after_seconds IS NULL OR (retry_after_seconds BETWEEN 0 AND 86400 "
        "AND provider_scope='llmgw' AND status='failed' AND receipt_hash IS NOT NULL "
        "AND usage_id IS NULL AND tokens_in IS NULL AND tokens_out IS NULL "
        "AND cache_read_tokens IS NULL AND cache_write_tokens IS NULL "
        "AND calculated_cost_rub IS NULL AND provider_cost_rub IS NULL "
        "AND provider_cost_usd IS NULL AND cost_provenance IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CHECK), _TABLE, type_="check")
    op.drop_column(_TABLE, "retry_after_seconds")
