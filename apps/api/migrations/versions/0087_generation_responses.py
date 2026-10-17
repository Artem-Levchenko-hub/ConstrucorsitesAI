"""Private response storage and quota accounting, independent of financial rows."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0087_generation_responses"
down_revision = "0086_generation_provider_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_response_budgets",
        sa.Column("bucket", sa.Text(), primary_key=True),
        sa.Column("storage_epoch", UUID(as_uuid=True), nullable=False),
        sa.Column("quota_bytes", sa.BigInteger(), nullable=False),
        sa.Column("allocated_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("observation_nonce", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("quota_bytes>0 AND allocated_bytes>=0", name="ck_response_budget_bytes"),
    )
    op.create_table(
        "generation_responses",
        sa.Column(
            "attempt_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_provider_attempts.attempt_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "bucket",
            sa.Text(),
            sa.ForeignKey("generation_response_budgets.bucket", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("writer_token", UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="uploading"),
        sa.Column("allocated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("stored_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("manifest_key", sa.Text()),
        sa.Column("manifest_sha256", sa.Text()),
        sa.Column("response_sha256", sa.Text()),
        sa.Column("reconciliation_hold", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("retain_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('uploading','complete','indeterminate','deleting','deleted')",
            name="ck_response_state",
        ),
        sa.CheckConstraint(
            "allocated_bytes>=used_bytes AND used_bytes>=stored_bytes AND stored_bytes>=0",
            name="ck_response_bytes",
        ),
        sa.CheckConstraint(
            "state<>'complete' OR (manifest_key IS NOT NULL AND manifest_sha256 IS NOT NULL AND response_sha256 IS NOT NULL)",
            name="ck_response_complete",
        ),
    )
    op.create_table(
        "generation_response_chunks",
        sa.Column(
            "attempt_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_responses.attempt_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("chunk_index", sa.BigInteger(), primary_key=True),
        sa.Column("object_key", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "kind IN ('chunks','manifest') AND chunk_index>=0 AND byte_length>=0 AND sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_response_chunk",
        ),
    )
    op.create_index("ix_response_cleanup", "generation_responses", ["state", "retain_until"])


def downgrade() -> None:
    # An in-flight writer must commit before checking evidence, never after it.
    op.execute(
        "LOCK TABLE generation_response_budgets, generation_responses, generation_response_chunks IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM generation_responses)")).scalar():
        raise RuntimeError("retained response evidence prevents downgrade")
    op.drop_table("generation_response_chunks")
    op.drop_table("generation_responses")
    op.drop_table("generation_response_budgets")
