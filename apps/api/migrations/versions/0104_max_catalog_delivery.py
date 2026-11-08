"""Keep delivery work with the saved MAX catalog version."""

import sqlalchemy as sa
from alembic import op

revision = "0104_max_catalog_delivery"
down_revision = "0103_max_staff_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("max_project_configs", sa.Column(
        "catalog_delivery_after", sa.DateTime(timezone=True), nullable=True,
    ))
    op.create_index(
        "ix_max_project_configs_catalog_delivery_after",
        "max_project_configs", ["catalog_delivery_after"],
    )


def downgrade() -> None:
    op.drop_index("ix_max_project_configs_catalog_delivery_after", table_name="max_project_configs")
    op.drop_column("max_project_configs", "catalog_delivery_after")
