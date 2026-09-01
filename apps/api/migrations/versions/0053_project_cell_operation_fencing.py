"""Harden Project Cell operation fencing and semantic idempotency.

Revision ID: 0053_project_cell_operation_fencing
Revises: 0052_project_cell_control_foundation
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import cast

import sqlalchemy as sa
from alembic import op

revision: str = "0053_project_cell_operation_fencing"
down_revision: str | None = "0052_project_cell_control_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_hex(payload: dict[str, object]) -> str:
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _operation_envelope(row: sa.RowMapping) -> dict[str, object]:
    request = row["request_payload"]
    if type(request) is not dict:
        raise RuntimeError("project_cell_operations.request_payload must be a JSON object")
    return {
        "workspace_id": str(row["workspace_id"]),
        "generation_run_id": (
            str(row["generation_run_id"]) if row["generation_run_id"] is not None else None
        ),
        "kind": row["kind"],
        "request": cast(dict[str, object], request),
    }


def _downgraded_request_payload(row: sa.RowMapping) -> dict[str, object]:
    payload = row["request_payload"]
    if type(payload) is not dict:
        raise RuntimeError("project_cell_operations.request_payload must be a JSON object")
    request = payload.get("request")
    if type(request) is not dict:
        raise RuntimeError("project_cell_operations.request_payload is not an operation envelope")
    return request


def _rewrite_request_payloads_to_envelopes() -> None:
    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                """
                SELECT id, workspace_id, generation_run_id, kind, request_payload
                FROM project_cell_operations
                ORDER BY created_at, id
                """
            )
        ).mappings()
    )
    for row in rows:
        envelope = _operation_envelope(row)
        connection.execute(
            sa.text(
                """
                UPDATE project_cell_operations
                SET request_payload = CAST(:request_payload AS JSONB),
                    request_digest = :request_digest
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "request_payload": _canonical_json(envelope),
                "request_digest": _sha256_hex(envelope),
            },
        )


def _rewrite_request_payloads_to_requests() -> None:
    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                """
                SELECT id, request_payload
                FROM project_cell_operations
                ORDER BY created_at, id
                """
            )
        ).mappings()
    )
    for row in rows:
        request = _downgraded_request_payload(row)
        connection.execute(
            sa.text(
                """
                UPDATE project_cell_operations
                SET request_payload = CAST(:request_payload AS JSONB),
                    request_digest = :request_digest
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "request_payload": _canonical_json(request),
                "request_digest": _sha256_hex(request),
            },
        )


def upgrade() -> None:
    op.add_column(
        "project_cell_operations",
        sa.Column("fencing_epoch", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE project_cell_operations AS operation
            SET fencing_epoch = workspace.fencing_epoch
            FROM project_cell_workspaces AS workspace
            WHERE operation.workspace_id = workspace.id
              AND operation.status = 'running'
            """
        )
    )
    _rewrite_request_payloads_to_envelopes()
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
        "kind IN ('ensure', 'wake', 'pause', 'stop', 'destroy', 'status', 'restore', 'reconcile')",
    )
    op.create_check_constraint(
        "status_allowed",
        "project_cell_operations",
        "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'indeterminate')",
    )


def downgrade() -> None:
    connection = op.get_bind()
    restore_rows = cast(
        int,
        connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM project_cell_operations
                WHERE kind IN ('restore', 'reconcile')
                """
            )
        ),
    )
    if restore_rows:
        raise RuntimeError(
            "cannot downgrade 0053 while restore/reconcile Project Cell operations exist"
        )

    connection.execute(
        sa.text(
            """
            UPDATE project_cell_operations
            SET status = 'failed'
            WHERE status = 'indeterminate'
            """
        )
    )
    _rewrite_request_payloads_to_requests()
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
        "kind IN ('ensure', 'wake', 'pause', 'stop', 'destroy', 'status')",
    )
    op.create_check_constraint(
        "status_allowed",
        "project_cell_operations",
        "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
    )
    op.drop_column("project_cell_operations", "fencing_epoch")
