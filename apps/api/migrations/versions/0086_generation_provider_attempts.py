"""Add off-default generation dispatch identity; never rewrite financial history."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0086_generation_provider_attempts"
down_revision = "0085_integration_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_provider_attempts",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        # Attribution survives app/user/run deletion; no cascading financial loss
        # and no FK key-share lock against the caller's existing run transaction.
        *[sa.Column(key, postgresql.UUID(as_uuid=True), nullable=False)
          for key in ("run_id", "project_id", "user_id", "dispatch_token")],
        *[sa.Column(key, sa.Text(), nullable=False) for key in ("prompt_hash", "request_sha256")],
        sa.Column("provider_call_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("provider_calls.id", ondelete="RESTRICT"), unique=True),
        sa.Column("parent_attempt_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("generation_provider_attempts.attempt_id", ondelete="RESTRICT"),
                  unique=True),
        sa.Column("phase", sa.Text(), nullable=False, server_default="reserved"),
        sa.Column("terminal_reason", sa.Text()),
        sa.Column("response_manifest_key", sa.Text()),
        sa.Column("response_sha256", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now() + interval '7 days'")),
        sa.CheckConstraint("version=1", name="ck_generation_attempt_version"),
        sa.CheckConstraint("prompt_hash ~ '^[0-9a-f]{64}$' AND request_sha256 ~ '^[0-9a-f]{64}$'",
                           name="ck_generation_attempt_digest"),
        sa.CheckConstraint("phase IN ('reserved','dispatching','response_stored',"
                           "'indeterminate','closed')", name="ck_generation_attempt_phase"),
        sa.CheckConstraint("(phase='reserved' AND provider_call_id IS NULL) OR "
                           "(phase<>'reserved' AND provider_call_id IS NOT NULL)",
                           name="ck_generation_attempt_call"),
        sa.CheckConstraint("terminal_reason IS NULL OR (phase='closed' AND terminal_reason IN "
                           "('completed','preconnect','rate_limit_refused'))",
                           name="ck_generation_attempt_terminal"),
        sa.CheckConstraint("parent_attempt_id IS NULL OR parent_attempt_id<>attempt_id",
                           name="ck_generation_attempt_parent"),
    )
    op.create_index("ix_generation_attempt_run", "generation_provider_attempts", ["run_id"])


def downgrade() -> None:
    # Retain dispatch/financial evidence instead of silently deleting open attempts.
    # Hold the same transaction's DDL lock before inspecting emptiness: a writer
    # that already started must commit/rollback before the evidence check runs.
    op.execute("LOCK TABLE generation_provider_attempts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM generation_provider_attempts)"))\
            .scalar():
        raise RuntimeError("generation provider evidence prevents downgrade")
    op.drop_table("generation_provider_attempts")
