"""Explicit owner opt-in for paid platform AI in public mini apps."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_project_runtime_ai"
down_revision: str | None = "0058_integration_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column(
        "runtime_ai_enabled", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))


def downgrade() -> None:
    op.drop_column("projects", "runtime_ai_enabled")
