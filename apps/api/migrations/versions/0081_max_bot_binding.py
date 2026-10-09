"""One identifiable MAX bot per project binding; never choose a duplicate winner."""

import sqlalchemy as sa
from alembic import op

revision = "0081_max_bot_binding"
down_revision = "0080_provider_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Serialize the assessment with writes until the constraint is installed.
    # Production operators first run the separate read-only preflight under drain.
    op.execute("LOCK TABLE max_integrations IN SHARE ROW EXCLUSIVE MODE")
    duplicates = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM (SELECT bot_id FROM max_integrations "
            "WHERE bot_id IS NOT NULL GROUP BY bot_id HAVING COUNT(*) > 1) duplicates"
        )
    )
    if duplicates:
        # No identifiers, tokens, row contents, deletions or winner selection.
        raise RuntimeError(
            f"max_bot_binding_duplicates: {duplicates} groups; "
            "resolve explicitly before retrying migration; existing bindings unchanged"
        )
    op.create_unique_constraint("uq_max_integrations_bot_id", "max_integrations", ["bot_id"])


def downgrade() -> None:
    op.drop_constraint("uq_max_integrations_bot_id", "max_integrations", type_="unique")
