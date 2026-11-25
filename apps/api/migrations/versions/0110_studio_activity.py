"""Deduplicate MAX studio activity and browser error flags per minute."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0110_studio_activity"
down_revision = "0109_aitunnel_app_ai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_activity",
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release", sa.String(length=40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("actor_id", "workspace_id", "minute", "release"),
    )
    op.create_index("ix_studio_activity_minute", "studio_activity", ["minute"])


def downgrade() -> None:
    op.drop_table("studio_activity")
