"""Add logical effect links; preserve all existing financial and source evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0088_generation_recovery_effects"
down_revision = "0087_generation_responses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_recovery_effects",
        sa.Column("effect_id", UUID(as_uuid=True), primary_key=True),
        *[
            sa.Column(key, UUID(as_uuid=True), nullable=False)
            for key in ("run_id", "project_id", "user_id", "claim_token")
        ],
        sa.Column("logical_step_seq", sa.BigInteger(), nullable=False),
        sa.Column("effect_ordinal", sa.Integer(), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="prepared"),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("attempt_seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "provider_attempt_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_provider_attempts.attempt_id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "parent_effect_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_recovery_effects.effect_id", ondelete="RESTRICT"),
        ),
        sa.Column("operation_id", UUID(as_uuid=True)),
        sa.Column("tool_call_id", sa.Text()),
        *[
            sa.Column(key, JSONB())
            for key in ("pre_binding", "post_binding", "receipt_ref", "checkpoint_ref")
        ],
        sa.Column("receipt_sha256", sa.Text()),
        *[
            sa.Column(key, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for key in ("created_at", "updated_at")
        ],
        sa.UniqueConstraint(
            "run_id", "logical_step_seq", "effect_ordinal", name="uq_recovery_effect_cursor"
        ),
        sa.CheckConstraint("payload_version=1", name="ck_recovery_effect_version"),
        sa.CheckConstraint(
            "logical_step_seq>=0 AND effect_ordinal>=0 AND revision>=0 AND attempt_seq>=0",
            name="ck_recovery_effect_counters",
        ),
        sa.CheckConstraint(
            "kind IN ('bootstrap','provider','tool','proof','progress')",
            name="ck_recovery_effect_kind",
        ),
        sa.CheckConstraint(
            "state IN ('prepared','inflight','confirmed','unknown','consumed')",
            name="ck_recovery_effect_state",
        ),
        sa.CheckConstraint("input_digest ~ '^[0-9a-f]{64}$'", name="ck_recovery_effect_digest"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE generation_recovery_effects IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM generation_recovery_effects)")):
        raise RuntimeError("generation recovery evidence prevents downgrade")
    op.drop_table("generation_recovery_effects")
