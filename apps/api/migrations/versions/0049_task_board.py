"""Add the shared four-person task board.

Revision ID: 0049_task_board
Revises: 0048_remove_telegram_reports
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_task_board"
down_revision: str | None = "0048_remove_telegram_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_board_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("assignee", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
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
            "status IN ('backlog', 'in_progress', 'review', 'done')",
            name="ck_task_board_tasks_valid_status",
        ),
        sa.CheckConstraint(
            "assignee IN ('alexey', 'alexey_jr', 'artem', 'roman')",
            name="ck_task_board_tasks_valid_assignee",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_task_board_tasks_valid_priority",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_board_tasks_status_position",
        "task_board_tasks",
        ["status", "position"],
    )
    op.execute(
        "CREATE TRIGGER task_board_tasks_set_updated_at "
        "BEFORE UPDATE ON task_board_tasks "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS task_board_tasks_set_updated_at ON task_board_tasks")
    op.drop_index("ix_task_board_tasks_status_position", table_name="task_board_tasks")
    op.drop_table("task_board_tasks")
