"""Add optional bounded business event types without rewriting historical receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0101_max_analytics_event_types"
down_revision = "0100_credit_adjustments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("max_analytics_events", sa.Column("event_type", sa.String(32), nullable=True))
    op.create_check_constraint(
        "max_analytics_event_type",
        "max_analytics_events",
        "event_type IS NULL OR "
        "(kind = 'open' AND event_type = 'app_open') OR "
        "(kind = 'action' AND event_type IN "
        "('form_submit','order_created','booking_created','lead_created')) OR "
        "(kind = 'event' AND event_type IN "
        "('screen_view','item_view','button_click','cart_add','checkout_start',"
        "'form_submit','order_created','booking_created','lead_created'))",
    )
    op.create_index(
        "ix_max_analytics_project_feed", "max_analytics_events", ["project_id", "occurred_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_max_analytics_project_feed", table_name="max_analytics_events")
    op.drop_constraint("max_analytics_event_type", "max_analytics_events", type_="check")
    op.drop_column("max_analytics_events", "event_type")
