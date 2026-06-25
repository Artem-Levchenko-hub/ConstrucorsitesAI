"""allow the `realtime` container template

G001 (ultragoal "enterprise real-time app generation"): a new container stack
`nextjs-realtime` — Next.js 15 + SSE/Redis pub-sub realtime hub + membership ACL
+ presence — that lets the generator build real multi-user real-time apps
(messengers, live-chat CRMs). Like every container stack it must be an allowed
`template` value so a project can be created/flipped onto it without violating
the `ck_projects_template_allowed` CHECK.

Mirrors 0004/0010/0017: DROP and re-ADD the CHECK (Postgres rewrites only the
constraint metadata — fast regardless of table size).

Revision ID: 0021_template_realtime
Revises: 0020_leads
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_template_realtime"
down_revision = "0020_leads"
branch_labels = None
depends_on = None


# State left by migration 0017 (added `code`).
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
    "code",
)
# Full set — must match the `Template` literal in schemas/project.py.
_NEW_TEMPLATES = (*_OLD_TEMPLATES, "realtime")
_CONSTRAINT = "ck_projects_template_allowed"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "projects", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "projects",
        "template IN " + str(tuple(_NEW_TEMPLATES)),
    )


def downgrade() -> None:
    # Rows on the now-disallowed `realtime` template would block re-adding the
    # narrower constraint. Rewrite them to "blank" (canonical state lives in git
    # anyway), mirroring 0010/0017's defensive downgrade.
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
