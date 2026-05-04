"""projects, snapshots, messages

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("current_snapshot_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("slug", name="uq_projects_slug"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_projects_owner_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100", name="ck_projects_name_length"
        ),
        sa.CheckConstraint(
            "template IN ('blank', 'landing', 'portfolio', 'blog')",
            name="ck_projects_template_allowed",
        ),
    )
    op.create_index(
        "ix_projects_owner_id_created_at",
        "projects",
        ["owner_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("parent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("preview_key", sa.Text(), nullable=True),
        sa.Column(
            "is_rollback_target",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_snapshots"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_snapshots_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["snapshots.id"], name="fk_snapshots_parent_id_snapshots"
        ),
        sa.CheckConstraint("char_length(commit_sha) = 40", name="ck_snapshots_commit_sha_len"),
    )
    op.create_index(
        "ix_snapshots_project_id_created_at",
        "snapshots",
        ["project_id", sa.text("created_at DESC")],
    )

    # Deferred FK projects.current_snapshot_id → snapshots.id (создаём после snapshots).
    op.create_foreign_key(
        "fk_projects_current_snapshot_id_snapshots",
        "projects",
        "snapshots",
        ["current_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_messages_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["snapshots.id"], name="fk_messages_snapshot_id_snapshots"
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name="ck_messages_role_allowed"
        ),
    )
    op.create_index(
        "ix_messages_project_id_created_at",
        "messages",
        ["project_id", sa.text("created_at ASC")],
    )

    op.execute(
        "CREATE TRIGGER projects_updated_at BEFORE UPDATE ON projects "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS projects_updated_at ON projects")
    op.drop_table("messages")
    op.drop_constraint(
        "fk_projects_current_snapshot_id_snapshots", "projects", type_="foreignkey"
    )
    op.drop_table("snapshots")
    op.drop_table("projects")
