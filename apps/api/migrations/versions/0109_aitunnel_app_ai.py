"""Keep explicit owner permission for general AITunnel requests."""

import sqlalchemy as sa
from alembic import op

revision = "0109_aitunnel_app_ai"
down_revision = "0108_provider_retry_after"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("runtime_aitunnel_ai_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("projects", "runtime_aitunnel_ai_enabled")
