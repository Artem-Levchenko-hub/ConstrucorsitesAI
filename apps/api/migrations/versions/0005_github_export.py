"""github_connections + projects github mirror columns

Adds the `github_connections` table (1:1 with users — stores the Fernet-encrypted
OAuth access token + GitHub username/scopes) and the `github_*` mirror columns on
`projects`, backing the "Export to GitHub" feature. The token is stored encrypted,
never in plaintext, so a DB leak does not hand an attacker push access to users' repos.

Revision ID: 0005_github_export
Revises: 0004_template_fullstack
Create Date: 2026-05-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005_github_export"
down_revision: str | None = "0004_template_fullstack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_connections",
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("github_username", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_github_connections"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_github_connections_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.execute(
        "CREATE TRIGGER github_connections_updated_at BEFORE UPDATE ON github_connections "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.add_column("projects", sa.Column("github_repo_full_name", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("github_repo_url", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("github_last_pushed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "github_last_pushed_at")
    op.drop_column("projects", "github_repo_url")
    op.drop_column("projects", "github_repo_full_name")
    op.execute("DROP TRIGGER IF EXISTS github_connections_updated_at ON github_connections")
    op.drop_table("github_connections")
