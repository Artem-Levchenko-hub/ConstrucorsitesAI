"""Вход через VK ID и Яндекс ID.

`user_identities` — связка аккаунта с провайдером: только его идентификатор
пользователя и снимок email (аккаунт без реквизитов). `oauth_login_states` —
серверное состояние рукопожатия (state, PKCE, одноразовый билет на экран
подтверждения документов) с TTL; токены провайдера не сохраняются.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_oauth_login"
down_revision: str | None = "0070_billing_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_user_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "provider", "provider_user_id", name="uq_user_identities_provider_subject"
        ),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])

    op.create_table(
        "oauth_login_states",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=True),
        sa.Column("next_path", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticket_hash", sa.Text(), nullable=True),
        sa.Column("ticket_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_provider_user_id", sa.Text(), nullable=True),
        sa.Column("pending_email", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("state_hash", name="uq_oauth_login_states_state_hash"),
        sa.UniqueConstraint("ticket_hash", name="uq_oauth_login_states_ticket_hash"),
    )
    op.create_index("ix_oauth_login_states_expires", "oauth_login_states", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_login_states_expires", table_name="oauth_login_states")
    op.drop_table("oauth_login_states")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
