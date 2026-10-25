"""Retain private fonts and immutable accepted font selections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0093_owner_fonts"
down_revision = "0092_reference_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "font_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploader_id", postgresql.UUID(as_uuid=True), nullable=True),
        *(
            sa.Column(name, sa.Text(), nullable=False)
            for name in ("object_key", "family", "filename", "sha256")
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("is_bound", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index("ix_font_assets_project_id", "font_assets", ["project_id"])
    op.create_index("ix_font_assets_uploader_id", "font_assets", ["uploader_id"])
    op.create_index("ix_font_assets_cleanup", "font_assets", ["status", "created_at"])
    for name in ("font_selections", "resolved_fonts"):
        op.add_column("messages", sa.Column(name, postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.execute("LOCK TABLE font_assets, messages IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM font_assets) OR EXISTS(SELECT 1 FROM messages "
            "WHERE COALESCE(font_selections,'[]'::jsonb) <> '[]'::jsonb "
            "OR COALESCE(resolved_fonts,'[]'::jsonb) <> '[]'::jsonb)"
        )
    ):
        raise RuntimeError("font_evidence_present: downgrade requires explicit resolution")
    op.drop_column("messages", "resolved_fonts")
    op.drop_column("messages", "font_selections")
    op.drop_table("font_assets")
