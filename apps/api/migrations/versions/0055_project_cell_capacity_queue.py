"""Add durable Project Cell capacity waiting.

Revision ID: 0055_project_cell_capacity_queue
Revises: 0054_project_cell_candidates
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_project_cell_capacity_queue"
down_revision: str | None = "0054_project_cell_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_generation_runs_status_allowed"), "generation_runs", type_="check"
    )
    op.create_check_constraint(
        "status_allowed",
        "generation_runs",
        "status IN ('pending', 'queued_for_capacity', 'running', 'cancel_requested', "
        "'cancelled', 'completed', 'failed')",
    )
    op.drop_index("uq_generation_runs_one_active_per_project", table_name="generation_runs")
    op.create_index(
        "uq_generation_runs_one_active_per_project",
        "generation_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'queued_for_capacity', 'running', 'cancel_requested')"
        ),
    )

    op.add_column(
        "project_cell_operations", sa.Column("capacity_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "project_cell_operations",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "project_cell_operations",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.drop_constraint(
        op.f("ck_project_cell_operations_kind_allowed"),
        "project_cell_operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_project_cell_operations_status_allowed"),
        "project_cell_operations",
        type_="check",
    )
    op.drop_index(
        "uq_project_cell_operations_one_active_per_workspace",
        table_name="project_cell_operations",
    )
    op.create_check_constraint(
        "kind_allowed",
        "project_cell_operations",
        "kind IN ('ensure', 'wake', 'pause', 'stop', 'destroy', 'status', 'restore', "
        "'reconcile', 'release')",
    )
    op.create_check_constraint(
        "status_allowed",
        "project_cell_operations",
        "status IN ('pending', 'waiting_capacity', 'running', 'completed', 'failed', "
        "'cancelled', 'indeterminate')",
    )
    op.create_check_constraint(
        "attempt_count_nonnegative",
        "project_cell_operations",
        "attempt_count >= 0",
    )
    op.create_index(
        "uq_project_cell_operations_one_active_per_workspace",
        "project_cell_operations",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'waiting_capacity', 'running')"),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE project_cell_operations
            SET status = 'failed',
                error = 'capacity queue migration downgraded',
                finished_at = now()
            WHERE status = 'waiting_capacity'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE generation_runs
            SET status = 'failed',
                error = 'capacity queue migration downgraded',
                finished_at = now()
            WHERE status = 'queued_for_capacity'
            """
        )
    )
    op.drop_index(
        "uq_project_cell_operations_one_active_per_workspace",
        table_name="project_cell_operations",
    )
    op.drop_constraint(
        op.f("ck_project_cell_operations_attempt_count_nonnegative"),
        "project_cell_operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_project_cell_operations_kind_allowed"),
        "project_cell_operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_project_cell_operations_status_allowed"),
        "project_cell_operations",
        type_="check",
    )
    op.create_check_constraint(
        "kind_allowed",
        "project_cell_operations",
        "kind IN ('ensure', 'wake', 'pause', 'stop', 'destroy', 'status', 'restore', "
        "'reconcile')",
    )
    op.create_check_constraint(
        "status_allowed",
        "project_cell_operations",
        "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'indeterminate')",
    )
    op.create_index(
        "uq_project_cell_operations_one_active_per_workspace",
        "project_cell_operations",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.drop_column("project_cell_operations", "attempt_count")
    op.drop_column("project_cell_operations", "next_attempt_at")
    op.drop_column("project_cell_operations", "capacity_reason")

    op.drop_index("uq_generation_runs_one_active_per_project", table_name="generation_runs")
    op.drop_constraint(
        op.f("ck_generation_runs_status_allowed"), "generation_runs", type_="check"
    )
    op.create_check_constraint(
        "status_allowed",
        "generation_runs",
        "status IN ('pending', 'running', 'cancel_requested', 'cancelled', 'completed', 'failed')",
    )
    op.create_index(
        "uq_generation_runs_one_active_per_project",
        "generation_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running', 'cancel_requested')"),
    )
