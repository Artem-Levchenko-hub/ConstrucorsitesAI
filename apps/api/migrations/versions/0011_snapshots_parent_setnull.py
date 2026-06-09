"""recreate snapshots.parent_id FK with ON DELETE SET NULL

The timeline self-reference snapshots.parent_id -> snapshots.id was created
without an ON DELETE action. Deleting a project cascades to its snapshots, but
the ORM emits row-by-row DELETEs; when a parent snapshot is removed while a
child still points at it, Postgres raises ForeignKeyViolationError and the whole
DELETE /api/projects/{id} 500s. Any project with a multi-snapshot timeline (i.e.
most real projects) becomes undeletable.

SET NULL fixes it: deleting a parent snapshot nulls the children's parent_id, so
teardown succeeds in any order. Since the children are themselves being deleted
in the same cascade, the transient NULL is harmless.

The FK name follows the metadata naming convention
(fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s) ->
fk_snapshots_parent_id_snapshots.

Revision ID: 0011_snapshots_parent_setnull
Revises: 0010_template_entity_stacks
Create Date: 2026-06-09

NOTE: revision id kept <= 32 chars — alembic_version.version_num is
varchar(32), so a longer slug (e.g. ..._fk_set_null) overflows on stamp.
"""

from __future__ import annotations

from alembic import op

revision = "0011_snapshots_parent_setnull"
down_revision = "0010_template_entity_stacks"
branch_labels = None
depends_on = None

_FK = "fk_snapshots_parent_id_snapshots"


def upgrade() -> None:
    op.drop_constraint(_FK, "snapshots", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "snapshots",
        "snapshots",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "snapshots", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "snapshots",
        "snapshots",
        ["parent_id"],
        ["id"],
    )
