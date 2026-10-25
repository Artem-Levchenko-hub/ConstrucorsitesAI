"""Keep private reference metadata and immutable message manifests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0092_reference_images"
down_revision = "0091_generation_credit_units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploader_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_bound", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index("ix_reference_images_project_id", "reference_images", ["project_id"])
    op.create_index("ix_reference_images_uploader_id", "reference_images", ["uploader_id"])
    op.create_index("ix_reference_images_cleanup", "reference_images", ["status", "created_at"])
    op.add_column("messages", sa.Column("reference_images", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    # Metadata is required to retain references and clean up private objects.
    # Never erase it or immutable message evidence to make an old binary start.
    op.execute("LOCK TABLE reference_images, messages IN ACCESS EXCLUSIVE MODE")
    present = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM reference_images) OR "
            "EXISTS(SELECT 1 FROM messages WHERE reference_images IS NOT NULL "
            "AND reference_images <> '[]'::jsonb)"
        )
    )
    if present:
        raise RuntimeError(
            "reference_image_evidence_present: downgrade requires explicit resolution"
        )
    op.drop_column("messages", "reference_images")
    op.drop_index("ix_reference_images_cleanup", table_name="reference_images")
    op.drop_index("ix_reference_images_uploader_id", table_name="reference_images")
    op.drop_index("ix_reference_images_project_id", table_name="reference_images")
    op.drop_table("reference_images")
