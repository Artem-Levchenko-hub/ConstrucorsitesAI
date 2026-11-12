"""Store an encrypted AITunnel key per MAX project."""

import sqlalchemy as sa
from alembic import op

revision = "0105_max_nutrition_ai"
down_revision = "0104_max_catalog_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("runtime_nutrition_ai_key_enc", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "runtime_nutrition_ai_key_enc")
