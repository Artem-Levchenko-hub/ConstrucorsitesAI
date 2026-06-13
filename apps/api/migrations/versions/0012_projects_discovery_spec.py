"""add projects.discovery_spec (reified onboarding chip answers)

Nullable JSONB column: stores the FidelitySpec the user steered the discovery
popup toward — ``{dark_mode, primary_family, sections, tone}`` — so downstream
acceptance gates can check the live render against what was actually picked
(V2.5 chip→build-spec causality). NULL = onboarding produced no assertable
signal; the build proceeds exactly as before.

Add is fast (nullable column, no default backfill, no constraint), safe on
large tables. No index — a whole-JSONB btree is useless and we only read the
column by project id.

Revision ID: 0012_projects_discovery_spec
Revises: 0011_snapshots_parent_setnull
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_projects_discovery_spec"
down_revision = "0011_snapshots_parent_setnull"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("discovery_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "discovery_spec")
