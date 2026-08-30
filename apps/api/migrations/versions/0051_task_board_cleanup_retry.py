"""Add retries and backoff to the attachment cleanup outbox.

Revision ID: 0051_task_board_cleanup_retry
Revises: 0050_task_board_attachments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_task_board_cleanup_retry"
down_revision: str | None = "0050_task_board_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_board_attachment_cleanup",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "task_board_attachment_cleanup",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "task_board_attachment_cleanup",
        sa.Column("last_error", sa.String(length=255), nullable=True),
    )
    op.create_check_constraint(
        "ck_task_board_attachment_cleanup_nonnegative_attempts",
        "task_board_attachment_cleanup",
        "attempts >= 0",
    )
    op.drop_index(
        "ix_task_board_attachment_cleanup_created",
        table_name="task_board_attachment_cleanup",
    )
    op.create_index(
        "ix_task_board_attachment_cleanup_due",
        "task_board_attachment_cleanup",
        ["next_attempt_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_board_attachment_cleanup_due",
        table_name="task_board_attachment_cleanup",
    )
    op.create_index(
        "ix_task_board_attachment_cleanup_created",
        "task_board_attachment_cleanup",
        ["created_at"],
    )
    op.drop_constraint(
        "ck_task_board_attachment_cleanup_nonnegative_attempts",
        "task_board_attachment_cleanup",
        type_="check",
    )
    op.drop_column("task_board_attachment_cleanup", "last_error")
    op.drop_column("task_board_attachment_cleanup", "next_attempt_at")
    op.drop_column("task_board_attachment_cleanup", "attempts")
