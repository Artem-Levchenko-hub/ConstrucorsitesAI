"""anon-project seam: ephemeral users + project lineage (V4.1a)

Backend contract for the viewer→creator half of pillar 4 ("forked own copy in
seconds, claim after value"). Three data-model facts:

* ``users.is_anon`` — BOOLEAN NOT NULL default false. Marks an ephemeral
  principal minted on unauthenticated project creation; a later ``claim``
  re-points the project to a real account and the anon row is left orphaned
  (cleanup is a separate concern).
* ``users.email`` / ``users.password_hash`` → made NULLABLE. An anon user has
  no credentials. The unique index on ``email`` already tolerates multiple
  NULLs (Postgres treats NULLs as distinct), so login/registration are
  unaffected — they only ever query non-null emails.
* ``projects.forked_from`` — nullable self-FK (ON DELETE SET NULL). Lineage
  foundation for V4.1b "Remix this" (deep-copy fork); NULL for every
  organically-created project.

All four changes are catalog-only (drop NOT NULL, add nullable column, add
constant-default boolean) — no table rewrite, safe on the live ``users`` /
``projects`` tables.

Revision ID: 0013_anon_project_seam
Revises: 0012_projects_discovery_spec
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT

revision = "0013_anon_project_seam"
down_revision = "0012_projects_discovery_spec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_anon",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("users", "email", existing_type=CITEXT(), nullable=True)
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)
    op.add_column(
        "projects",
        sa.Column(
            "forked_from",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_projects_forked_from",
        "projects",
        "projects",
        ["forked_from"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_projects_forked_from", "projects", type_="foreignkey")
    op.drop_column("projects", "forked_from")
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
    op.alter_column("users", "email", existing_type=CITEXT(), nullable=False)
    op.drop_column("users", "is_anon")
