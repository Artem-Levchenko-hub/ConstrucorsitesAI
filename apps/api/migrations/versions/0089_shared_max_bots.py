"""Allow a validated MAX bot in independent project integrations."""

import sqlalchemy as sa
from alembic import op

revision = "0089_shared_max_bots"
down_revision = "0088_generation_recovery_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_max_integrations_bot_id", "max_integrations", type_="unique")


def downgrade() -> None:
    op.execute("LOCK TABLE max_integrations IN SHARE ROW EXCLUSIVE MODE")
    duplicates = op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM max_integrations WHERE bot_id IS NOT NULL "
        "GROUP BY bot_id HAVING COUNT(*) > 1)"
    ))
    if duplicates:
        raise RuntimeError("shared_max_bots_present: downgrade requires explicit resolution")
    op.create_unique_constraint("uq_max_integrations_bot_id", "max_integrations", ["bot_id"])
