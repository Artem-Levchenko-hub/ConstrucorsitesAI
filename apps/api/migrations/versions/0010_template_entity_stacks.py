"""allow the entity-engine + extra container stacks as project templates

Migration 0004 widened the `template` CHECK constraint to include
`fullstack`. Since then the code grew four more container-backed stacks —
`nextjs_entities` (Base44-style entity engine), `spa`, `tgbot`, `api` —
in the Pydantic `Template` literal, the new-project UI and the
orchestrator template map, but the DB CHECK constraint was never
widened to match. Result: inserting a project with any of those
templates fails with `ck_projects_template_allowed` violation, blocking
the whole entity-engine stack in production.

This migration realigns the constraint with the `Template` literal in
`apps/api/src/omnia_api/schemas/project.py`. The allowlist is hardcoded
(not imported from app code) so the migration stays a frozen point-in-time
snapshot. As in 0004 we DROP and re-ADD — Postgres rewrites only the
constraint metadata, so it's fast regardless of table size.

Revision ID: 0010_template_entity_stacks
Revises: 0009_users_free_generations_used
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_template_entity_stacks"
down_revision = "0009_users_free_generations_used"
branch_labels = None
depends_on = None


# State left by migration 0004.
_OLD_TEMPLATES = ("blank", "landing", "portfolio", "blog", "fullstack")
# Full set — must match the `Template` literal in schemas/project.py.
_NEW_TEMPLATES = (
    "blank",
    "landing",
    "portfolio",
    "blog",
    "fullstack",
    "nextjs_entities",
    "spa",
    "tgbot",
    "api",
)
_CONSTRAINT = "ck_projects_template_allowed"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "projects", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "projects",
        "template IN " + str(tuple(_NEW_TEMPLATES)),
    )


def downgrade() -> None:
    # Rows on a now-disallowed template would block re-adding the narrower
    # constraint. Rewrite them to "blank" (canonical state lives in git anyway),
    # mirroring 0004's defensive downgrade.
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
