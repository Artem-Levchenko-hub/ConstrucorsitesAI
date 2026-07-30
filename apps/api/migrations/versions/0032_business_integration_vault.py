"""Business integration vault, project bindings and OAuth state.

Revision ID: 0032_business_integration_vault
Revises: 0031_app_integrations
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_business_integration_vault"
down_revision = "0031_app_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_integrations",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "app_integrations",
        sa.Column("auth_mode", sa.Text(), nullable=False, server_default="credentials"),
    )
    op.add_column(
        "app_integrations",
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE app_integrations AS ai
        SET business_id = bm.business_id
        FROM business_members AS bm
        WHERE bm.user_id = ai.owner_id
        """
    )
    # MAX Studio requires a verified business. Any pre-existing orphan cannot be
    # reused safely across a business and is therefore removed instead of being
    # silently attached to another legal entity.
    op.execute("DELETE FROM app_integrations WHERE business_id IS NULL")

    op.create_table(
        "project_integration_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="ready"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'needs_setup', 'error')",
            name="ck_project_integration_bindings_status",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["app_integrations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_integration_bindings")),
        sa.UniqueConstraint(
            "project_id",
            "provider",
            name="uq_project_integration_bindings_project_provider",
        ),
    )
    op.create_index(
        "ix_project_integration_bindings_integration",
        "project_integration_bindings",
        ["integration_id"],
        unique=False,
    )
    # Preserve every old project attachment while deduplicating credentials at
    # business level. The most recently verified record becomes the vault item.
    op.execute(
        """
        WITH ranked AS (
          SELECT id, project_id, provider,
                 FIRST_VALUE(id) OVER (
                   PARTITION BY business_id, provider
                   ORDER BY verified_at DESC NULLS LAST, updated_at DESC, id
                 ) AS keeper_id
          FROM app_integrations
        )
        INSERT INTO project_integration_bindings
          (id, project_id, integration_id, provider, enabled, status)
        SELECT uuid_generate_v4(), project_id, keeper_id, provider, TRUE, 'ready'
        FROM ranked
        ON CONFLICT (project_id, provider) DO NOTHING
        """
    )
    op.execute(
        """
        DELETE FROM app_integrations ai
        USING app_integrations newer
        WHERE ai.business_id = newer.business_id
          AND ai.provider = newer.provider
          AND (
            COALESCE(ai.verified_at, '-infinity'::timestamptz),
            ai.updated_at,
            ai.id
          ) < (
            COALESCE(newer.verified_at, '-infinity'::timestamptz),
            newer.updated_at,
            newer.id
          )
        """
    )

    op.drop_constraint(
        "uq_app_integrations_project_provider", "app_integrations", type_="unique"
    )
    op.drop_index("ix_app_integrations_project_id", table_name="app_integrations")
    op.drop_index("ix_app_integrations_owner_id", table_name="app_integrations")
    op.drop_constraint(
        "fk_app_integrations_project_id_projects",
        "app_integrations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_app_integrations_owner_id_users",
        "app_integrations",
        type_="foreignkey",
    )
    op.drop_column("app_integrations", "project_id")
    op.alter_column(
        "app_integrations", "owner_id", new_column_name="created_by_user_id"
    )
    op.alter_column("app_integrations", "business_id", nullable=False)
    op.create_foreign_key(
        "fk_app_integrations_business_id_business_profiles",
        "app_integrations",
        "business_profiles",
        ["business_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_app_integrations_created_by_user_id_users",
        "app_integrations",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_app_integrations_business_provider",
        "app_integrations",
        ["business_id", "provider"],
    )
    op.create_index(
        "ix_app_integrations_business_id",
        "app_integrations",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_app_integrations_created_by",
        "app_integrations",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_app_integrations_auth_mode_allowed",
        "app_integrations",
        "auth_mode IN ('credentials', 'oauth', 'connector')",
    )

    op.create_table(
        "integration_oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["business_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_oauth_states")),
        sa.UniqueConstraint(
            "state_hash", name=op.f("uq_integration_oauth_states_state_hash")
        ),
    )
    op.create_index(
        "ix_integration_oauth_states_expires",
        "integration_oauth_states",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "0032 contains a business-level deduplication and cannot be downgraded safely"
    )
