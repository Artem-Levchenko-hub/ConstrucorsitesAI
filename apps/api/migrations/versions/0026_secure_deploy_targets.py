"""Secure BYO target verification metadata.

Revision ID: 0026_secure_deploy_targets
Revises: 0025_generation_runs
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_secure_deploy_targets"
down_revision = "0025_generation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_deploy_targets_verify_status_allowed",
        "deploy_targets",
        type_="check",
    )
    op.create_check_constraint(
        "ck_deploy_targets_verify_status_allowed",
        "deploy_targets",
        "verify_status IN ('unverified', 'pending_confirmation', 'ok', 'failed')",
    )
    op.add_column("deploy_targets", sa.Column("host_fingerprint", sa.Text(), nullable=True))
    op.add_column("deploy_targets", sa.Column("resolved_ip", sa.Text(), nullable=True))
    op.add_column(
        "deploy_targets",
        sa.Column("capabilities", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("previous_deploy_target_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_previous_deploy_target_id",
        "projects",
        "deploy_targets",
        ["previous_deploy_target_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_projects_previous_deploy_target_id", "projects", type_="foreignkey")
    op.drop_column("projects", "previous_deploy_target_id")
    op.execute(
        "UPDATE deploy_targets SET verify_status='unverified' "
        "WHERE verify_status='pending_confirmation'"
    )
    op.drop_column("deploy_targets", "capabilities")
    op.drop_column("deploy_targets", "resolved_ip")
    op.drop_column("deploy_targets", "host_fingerprint")
    op.drop_constraint(
        "ck_deploy_targets_verify_status_allowed",
        "deploy_targets",
        type_="check",
    )
    op.create_check_constraint(
        "ck_deploy_targets_verify_status_allowed",
        "deploy_targets",
        "verify_status IN ('unverified', 'ok', 'failed')",
    )
