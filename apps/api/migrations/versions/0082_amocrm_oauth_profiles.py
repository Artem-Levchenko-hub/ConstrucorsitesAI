"""Keep the originating amoCRM OAuth profile without rewriting existing grants."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0082_amocrm_oauth_profiles"
down_revision = "0081_max_bot_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_integrations", sa.Column("oauth_client_key", sa.Text(), nullable=True))
    op.add_column(
        "app_integrations",
        sa.Column("grant_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("integration_oauth_states", sa.Column("oauth_client_key", sa.Text(), nullable=True))
    op.add_column(
        "integration_oauth_states", sa.Column("connection_snapshot", JSONB(), nullable=True)
    )


def downgrade() -> None:
    # An older binary would refresh a public grant with the legacy application.
    # Refuse loss of its identity, including unknown keys and pending guarded flows.
    op.execute("LOCK TABLE app_integrations, integration_oauth_states IN ACCESS EXCLUSIVE MODE")
    in_use = op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM app_integrations "
        "WHERE oauth_client_key IS NOT NULL AND oauth_client_key <> 'amocrm-legacy-v1') "
        "OR EXISTS(SELECT 1 FROM integration_oauth_states "
        "WHERE (oauth_client_key IS NOT NULL AND oauth_client_key <> 'amocrm-legacy-v1') "
        "OR (connection_snapshot IS NOT NULL AND used_at IS NULL AND expires_at > now()))"
    ))
    if in_use:
        raise RuntimeError("oauth_profiles_in_use; retain compatible readers and disable new starts")
    op.drop_column("integration_oauth_states", "connection_snapshot")
    op.drop_column("integration_oauth_states", "oauth_client_key")
    op.drop_column("app_integrations", "grant_revision")
    op.drop_column("app_integrations", "oauth_client_key")
