"""allow 'fullstack' as a project template

The original CHECK constraint added in migration 0002 listed only V1
static templates. V2 Phase A introduces a `fullstack` template (Next.js
+ Postgres + Drizzle, runs in an orchestrator-managed dev container) and
its INSERTs need to pass the CHECK. We DROP and re-ADD the constraint
with the expanded allowlist; both operations are fast even on large
tables because Postgres rewrites only the constraint metadata.

Revision ID: 0004_template_fullstack
Revises: 0003
Create Date: 2026-05-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_template_fullstack"
down_revision = "0003"
branch_labels = None
depends_on = None


_OLD_TEMPLATES = ("blank", "landing", "portfolio", "blog")
_NEW_TEMPLATES = ("blank", "landing", "portfolio", "blog", "fullstack")
_CONSTRAINT = "ck_projects_template_allowed"


def upgrade() -> None:
    # Drop the existing CHECK ...
    op.drop_constraint(_CONSTRAINT, "projects", type_="check")
    # ... and re-add with `fullstack` in the allowlist.
    op.create_check_constraint(
        _CONSTRAINT,
        "projects",
        "template IN " + str(tuple(_NEW_TEMPLATES)),
    )


def downgrade() -> None:
    # Be defensive: a downgrade with existing fullstack rows would otherwise
    # leave the table in a state Postgres refuses to add the old constraint
    # against. Rewrite them to "blank" (data is in git anyway).
    op.execute(
        sa.text(
            "UPDATE projects SET template = 'blank' "
            "WHERE template NOT IN " + str(tuple(_OLD_TEMPLATES))
        )
    )
    op.drop_constraint(_CONSTRAINT, "projects", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "projects",
        "template IN " + str(tuple(_OLD_TEMPLATES)),
    )
