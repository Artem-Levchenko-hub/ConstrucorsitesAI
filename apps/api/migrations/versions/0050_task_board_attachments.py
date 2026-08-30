"""Add private file attachments to task-board cards.

Revision ID: 0050_task_board_attachments
Revises: 0049_task_board
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_task_board_attachments"
down_revision: str | None = "0049_task_board"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_board_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=160), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("size > 0", name="ck_task_board_attachments_positive_size"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task_board_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_task_board_attachments_task_created",
        "task_board_attachments",
        ["task_id", "created_at"],
    )
    op.create_table(
        "task_board_attachment_cleanup",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "size > 0",
            name="ck_task_board_attachment_cleanup_positive_size",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_task_board_attachment_cleanup_created",
        "task_board_attachment_cleanup",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_board_attachment_cleanup_created",
        table_name="task_board_attachment_cleanup",
    )
    op.drop_table("task_board_attachment_cleanup")
    op.drop_index(
        "ix_task_board_attachments_task_created",
        table_name="task_board_attachments",
    )
    op.drop_table("task_board_attachments")
