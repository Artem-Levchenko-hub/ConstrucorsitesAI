"""Add account-scoped unlimited generation and grant creator privileges.

Revision ID: 0044_creator_account_privileges
Revises: 0043_max_instant_demo
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_creator_account_privileges"
down_revision = "0043_max_instant_demo"
branch_labels = None
depends_on = None

CREATOR_EMAIL = "undj00x03@gmail.com"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "unlimited_generations",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # This is an explicit owner-directed production grant, not a role-wide
    # implication. A truly empty fresh install has no production account to
    # grant and must remain bootstrap-able by the disposable release gate. Once
    # any account exists, keep the original fail-closed production invariant.
    connection = op.get_bind()
    existing_user_count = connection.scalar(sa.text("SELECT count(*) FROM users"))
    if existing_user_count == 0:
        return
    grant_result = connection.execute(
        sa.text(
            """
            UPDATE users
            SET role = 'admin', unlimited_generations = true
            WHERE email = :email AND is_anon = false
            """
        ),
        {"email": CREATOR_EMAIL},
    )
    if grant_result.rowcount != 1:
        raise RuntimeError(
            "creator privilege grant expected exactly one existing account "
            f"for {CREATOR_EMAIL}, updated {grant_result.rowcount}"
        )
    connection.execute(
        sa.text(
            """
            INSERT INTO admin_audit_events (
                id, actor_user_id, target_user_id, action, details
            )
            SELECT
                uuid_generate_v4(), id, id, 'creator.privileges.bootstrap',
                jsonb_build_object(
                    'source', 'migration:0044_creator_account_privileges',
                    'after', jsonb_build_object(
                        'role', role,
                        'unlimited_generations', unlimited_generations
                    )
                )
            FROM users
            WHERE email = :email AND is_anon = false
            """
        ),
        {"email": CREATOR_EMAIL},
    )


def downgrade() -> None:
    # Do not silently revoke the administrator role: it may have been confirmed
    # independently after this migration.  Removing the entitlement column is
    # the only reversible schema operation.
    op.drop_column("users", "unlimited_generations")
