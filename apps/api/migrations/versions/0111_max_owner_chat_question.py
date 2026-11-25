"""Identify owner questions and answers in MAX chat history."""

import sqlalchemy as sa
from alembic import op

revision = "0111_max_owner_chat_question"
down_revision = "0110_studio_activity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages", sa.Column("chat_question", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("messages", "chat_question")
