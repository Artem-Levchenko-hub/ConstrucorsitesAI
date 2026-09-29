"""Durable generation admission fence during deployment."""

import sqlalchemy as sa
from alembic import op

revision = "0073_generation_deployment_drain"
down_revision = "0072_moysklad_vendor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployment_drains",
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("release_sha", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("deployment_drains")
