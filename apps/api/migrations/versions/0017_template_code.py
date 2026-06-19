"""allow the language-agnostic `code` template

Owner directive 2026-06-18: don't lock the builder to web output. When the
user asks for a script / program in ANY language (Python, Go, Rust, a CLI,
a parser, …) we route the project to a new `code` template — a file-only,
no-container surface that stores arbitrary source like GitHub does (written,
versioned, downloadable, GitHub-pushable), instead of forcing a website.

`code` is NOT container-backed (it has no orchestrator image — see
`schemas/project.py._ORCHESTRATOR_TEMPLATE_BY_API`, which intentionally omits
it). It only needs to be an allowed `template` value so stack-routing can flip
a fresh project onto it. As in 0010/0004 we DROP and re-ADD the CHECK — Postgres
rewrites only the constraint metadata, fast regardless of table size.

Revision ID: 0017_template_code
Revises: 0016_projects_discovery_plan
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_template_code"
down_revision = "0016_projects_discovery_plan"
branch_labels = None
depends_on = None


# State left by migration 0010.
_OLD_TEMPLATES = (
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
# Full set — must match the `Template` literal in schemas/project.py.
_NEW_TEMPLATES = (*_OLD_TEMPLATES, "code")
_CONSTRAINT = "ck_projects_template_allowed"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "projects", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "projects",
        "template IN " + str(tuple(_NEW_TEMPLATES)),
    )


def downgrade() -> None:
    # Rows on the now-disallowed `code` template would block re-adding the
    # narrower constraint. Rewrite them to "blank" (canonical state lives in
    # git anyway), mirroring 0010's defensive downgrade.
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
