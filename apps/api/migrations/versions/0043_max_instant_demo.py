"""Add a server-owned MAX instant-demo allowance.

Revision ID: 0043_max_instant_demo
Revises: 0042_project_runtime_sync_guard
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_max_instant_demo"
down_revision = "0042_project_runtime_sync_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "max_demo_generations_used",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_users_max_demo_generations_non_negative",
        "users",
        "max_demo_generations_used >= 0",
    )
    # Do not mint a fresh demo for accounts that already completed a MAX build
    # before this counter existed. Starter/config snapshots have no prompt and
    # therefore do not consume the new allowance.
    op.execute(
        """
        UPDATE users AS u
        SET max_demo_generations_used = 1
        WHERE EXISTS (
            SELECT 1
            FROM projects AS p
            JOIN snapshots AS s ON s.project_id = p.id
            WHERE p.owner_id = u.id
              AND p.template = 'max_miniapp'
              AND s.prompt_text IS NOT NULL
              AND length(trim(s.prompt_text)) > 0
        )
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_users_max_demo_generations_non_negative",
        "users",
        type_="check",
    )
    op.drop_column("users", "max_demo_generations_used")
