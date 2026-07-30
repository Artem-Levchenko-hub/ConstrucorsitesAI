"""Track the managed MAX kit revision applied to each project.

Revision ID: 0033_max_managed_kit_version
Revises: 0032_business_integration_vault
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_max_managed_kit_version"
down_revision = "0032_business_integration_vault"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing projects start at revision 1 so the API applies the current kit
    # once, even when their business configuration itself has not changed.
    op.add_column(
        "max_project_configs",
        sa.Column(
            "managed_kit_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("max_project_configs", "managed_kit_version")
