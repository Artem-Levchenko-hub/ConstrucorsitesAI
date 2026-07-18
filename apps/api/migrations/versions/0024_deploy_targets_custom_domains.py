"""BYO-VPS + свой домен: deploy_targets, custom_domains, projects.deploy_target_id

Revision ID: 0024_deploy_targets_domains
Revises: 0023_messages_agent_steps
Create Date: 2026-07-18

Аддитивная миграция: две новые таблицы + одна nullable-колонка. Существующие
проекты получают deploy_target_id = NULL (= наш хостинг), поведение не меняется.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_deploy_targets_domains"
down_revision = "0023_messages_agent_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deploy_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("ssh_host", sa.Text(), nullable=False),
        sa.Column("ssh_port", sa.Integer(), server_default="22", nullable=False),
        sa.Column("ssh_user", sa.Text(), nullable=False),
        sa.Column("ssh_auth_type", sa.Text(), nullable=False),
        sa.Column("ssh_secret_enc", sa.Text(), nullable=False),
        sa.Column("ssh_public_key", sa.Text(), nullable=True),
        sa.Column("known_host_key", sa.Text(), nullable=True),
        sa.Column(
            "verify_status", sa.Text(), server_default="unverified", nullable=False
        ),
        sa.Column("verify_detail", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ssh_auth_type IN ('key', 'password')",
            name="ck_deploy_targets_auth_type_allowed",
        ),
        sa.CheckConstraint(
            "verify_status IN ('unverified', 'ok', 'failed')",
            name="ck_deploy_targets_verify_status_allowed",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deploy_targets_owner_id", "deploy_targets", ["owner_id"])

    op.create_table(
        "custom_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), server_default="external", nullable=False),
        sa.Column("expected_ip", sa.Text(), nullable=False),
        sa.Column("dns_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("cert_status", sa.Text(), server_default="none", nullable=False),
        sa.Column("last_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source IN ('external', 'purchased')",
            name="ck_custom_domains_source_allowed",
        ),
        sa.CheckConstraint(
            "dns_status IN ('pending', 'ok', 'mismatch')",
            name="ck_custom_domains_dns_status_allowed",
        ),
        sa.CheckConstraint(
            "cert_status IN ('none', 'issuing', 'active', 'failed')",
            name="ck_custom_domains_cert_status_allowed",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host", name="uq_custom_domains_host"),
    )
    op.create_index("ix_custom_domains_project_id", "custom_domains", ["project_id"])

    op.add_column(
        "projects",
        sa.Column("deploy_target_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_deploy_target_id_deploy_targets",
        "projects",
        "deploy_targets",
        ["deploy_target_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_projects_deploy_target_id_deploy_targets", "projects", type_="foreignkey"
    )
    op.drop_column("projects", "deploy_target_id")
    op.drop_index("ix_custom_domains_project_id", table_name="custom_domains")
    op.drop_table("custom_domains")
    op.drop_index("ix_deploy_targets_owner_id", table_name="deploy_targets")
    op.drop_table("deploy_targets")
