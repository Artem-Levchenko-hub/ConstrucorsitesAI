"""add projects.discovery_plan (pre-computed onboarding question batch)

Nullable JSONB column: stores the batch of 3–4 product-tailored discovery
questions planned in ONE upfront LLM pass right after the first prompt — a list
of ``{message, choices, allow_custom, multi_select}`` — so each is served one per
turn with NO further gateway call (owner rule 13 #1, NORTH STAR pillar 2: zero
wait between onboarding questions). NULL = the batch path wasn't used (or a
zero-question immediate build); discovery falls back to per-question conversation.

Add is fast (nullable column, no default backfill, no constraint), safe on large
tables. No index — a whole-JSONB btree is useless and we only read the column by
project id.

Revision ID: 0016_projects_discovery_plan
Revises: 0015_projects_viral_eligible
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_projects_discovery_plan"
down_revision = "0015_projects_viral_eligible"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("discovery_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "discovery_plan")
