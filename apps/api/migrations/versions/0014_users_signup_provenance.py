"""users signup provenance: viral return-edge source + referrer (V4.2b)

Backend half of the pillar-4 PROVENANCE assert ("signup-строка несёт
source=='share_link' + referrer_project_id==viewed"). Two nullable columns on
``users``:

* ``signup_source`` — TEXT NULL. Bounded at the API layer to a closed enum
  (share_link | remix | direct); NULL for organic signups. The NULL is the
  falsifiable "the link, not a default, drives provenance" signal.
* ``referrer_project_id`` — nullable UUID, NO foreign key. A soft analytics
  pointer to the SOURCE project the visitor was viewing; deliberately FK-less so
  deleting that project never orphans/blocks a signup history row.

Both are additive nullable columns (catalog-only, no table rewrite) → safe on
the live ``users`` table with no backfill and no downtime.

Revision ID: 0014_users_signup_provenance
Revises: 0013_anon_project_seam
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_users_signup_provenance"
down_revision = "0013_anon_project_seam"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("signup_source", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "referrer_project_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "referrer_project_id")
    op.drop_column("users", "signup_source")
