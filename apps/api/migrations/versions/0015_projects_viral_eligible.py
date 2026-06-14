"""add projects.viral_eligible (V4.9 — beauty-floor gate for the viral pool)

A project enters the viral pool — i.e. a zero-signup fork of it inherits the
right to itself be re-shared — only when its rendered surface clears the
pillar-1 floor (taste + hierarchy, plus first-paint when the stranger-cold path
measures it). The live composition gate (``workers.quality``) stamps this flag
after it scores a freshly generated/edited app; ``perform_fork`` carries it onto
the fork so the pool is transitively floor-gated (the V4.7 fork-tree invariant:
every node must be WOW-green).

Defaults FALSE — a project is not vouched for until a gate has actually scored
it. Add is fast (boolean column with server-default, existing rows pick up the
default; nothing organically eligible until re-scored).

Revision ID: 0015_projects_viral_eligible
Revises: 0014_users_signup_provenance
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_projects_viral_eligible"
down_revision = "0014_users_signup_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "viral_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "viral_eligible")
