"""Stage server-enforced amoCRM consent without changing existing projects."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0085_integration_consent"
down_revision = "0084_billing_catalog_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_consent_policies",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("policy_hash", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "integration_consent_states",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("max_user_id", sa.Text(), primary_key=True),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("policy_hash", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("revision > 0", name="ck_integration_consent_revision"),
    )
    op.create_table(
        "integration_consent_events",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("max_user_id", sa.Text(), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("policy_hash", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("revision > 0", name="ck_integration_consent_event_revision"),
    )


def downgrade() -> None:
    op.drop_table("integration_consent_events")
    op.drop_table("integration_consent_states")
    op.drop_table("integration_consent_policies")
