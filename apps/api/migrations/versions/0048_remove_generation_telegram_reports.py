"""Remove the retired generation Telegram observer state.

Revision ID: 0048_remove_telegram_reports
Revises: 0047_generation_telegram_reports
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_remove_telegram_reports"
down_revision: str | None = "0047_generation_telegram_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DROP TRIGGER generation_telegram_reports_set_updated_at "
        "ON generation_telegram_reports"
    )
    op.drop_index(
        "ix_generation_telegram_reports_due_work",
        table_name="generation_telegram_reports",
    )
    op.drop_table("generation_telegram_reports")


def downgrade() -> None:
    op.create_table(
        "generation_telegram_reports",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "start_state",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("start_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "finish_state",
            sa.Text(),
            server_default=sa.text("'waiting_terminal'"),
            nullable=False,
        ),
        sa.Column("terminal_status", sa.Text(), nullable=True),
        sa.Column(
            "last_stage",
            sa.Text(),
            server_default=sa.text("'accepted'"),
            nullable=False,
        ),
        sa.Column(
            "start_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "finish_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("start_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finish_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivery_error_code", sa.Text(), nullable=True),
        sa.Column("preview_error_code", sa.Text(), nullable=True),
        sa.Column("preview_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "start_state IN ('pending', 'sending', 'sent', 'failed', 'suppressed')",
            name="ck_generation_telegram_reports_start_state",
        ),
        sa.CheckConstraint(
            "finish_state IN ('waiting_terminal', 'waiting_preview', 'pending', "
            "'sending', 'sent', 'warning_sent', 'failed', 'suppressed')",
            name="ck_generation_telegram_reports_finish_state",
        ),
        sa.CheckConstraint(
            "terminal_status IS NULL OR terminal_status IN "
            "('completed', 'failed', 'cancelled')",
            name="ck_generation_telegram_reports_terminal_status",
        ),
        sa.CheckConstraint(
            "last_stage IN ('accepted', 'routing', 'director', 'writer', 'images', "
            "'acceptance', 'snapshot', 'preview')",
            name="ck_generation_telegram_reports_last_stage",
        ),
        sa.CheckConstraint(
            "start_attempts >= 0",
            name="ck_generation_telegram_reports_start_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "finish_attempts >= 0",
            name="ck_generation_telegram_reports_finish_attempts_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["generation_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_generation_telegram_reports_due_work",
        "generation_telegram_reports",
        [
            "start_state",
            "finish_state",
            "start_next_attempt_at",
            "finish_next_attempt_at",
            "lease_until",
        ],
    )
    op.execute(
        "CREATE TRIGGER generation_telegram_reports_set_updated_at "
        "BEFORE UPDATE ON generation_telegram_reports "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
