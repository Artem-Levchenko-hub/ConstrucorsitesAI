"""Add bounded email verification codes while preserving existing links."""

import sqlalchemy as sa
from alembic import op

revision = "0083_email_verification_codes"
down_revision = "0082_amocrm_oauth_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_tokens", sa.Column("code_hash", sa.Text(), nullable=True))
    op.add_column(
        "auth_tokens", sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "auth_tokens", sa.Column("code_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_check_constraint(
        op.f("ck_auth_tokens_code_attempts"),
        "auth_tokens",
        "code_attempts >= 0 AND code_attempts <= 5",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_auth_tokens_code_attempts"), "auth_tokens", type_="check")
    op.drop_column("auth_tokens", "code_attempts")
    op.drop_column("auth_tokens", "code_expires_at")
    op.drop_column("auth_tokens", "code_hash")
