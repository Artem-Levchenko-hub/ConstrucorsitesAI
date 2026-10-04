"""Privacy-safe first-party signed MAX analytics receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0076_max_analytics"
down_revision = "0075_generation_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "max_analytics_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_key", sa.String(64), nullable=False),
        sa.Column("event_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("project_id", "actor_key", "event_id", name="uq_max_analytics_receipt"),
        sa.CheckConstraint("kind IN ('open','action','event')", name="max_analytics_kind"),
    )
    op.create_index(
        "ix_max_analytics_project_time", "max_analytics_events", ["project_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_table("max_analytics_events")
