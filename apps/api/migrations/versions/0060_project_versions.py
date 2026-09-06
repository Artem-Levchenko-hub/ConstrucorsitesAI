"""Permanent user-facing versions and provenance-bound snapshot images."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060_project_versions"
down_revision: str | None = "0059_project_runtime_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "snapshots",
        sa.Column(
            "preview_manifest",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "snapshots",
        sa.Column("preview_status", sa.Text(), nullable=False, server_default="missing"),
    )
    op.add_column("snapshots", sa.Column("preview_commit_sha", sa.Text(), nullable=True))
    op.create_table(
        "project_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "source_message_id", sa.UUID(), sa.ForeignKey("messages.id", ondelete="SET NULL")
        ),
        sa.Column(
            "generation_run_id",
            sa.UUID(),
            sa.ForeignKey("generation_runs.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("snapshot_id", sa.UUID(), sa.ForeignKey("snapshots.id", ondelete="SET NULL")),
        sa.Column(
            "base_snapshot_id", sa.UUID(), sa.ForeignKey("snapshots.id", ondelete="SET NULL")
        ),
        sa.Column(
            "restored_from_snapshot_id",
            sa.UUID(),
            sa.ForeignKey("snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column("commit_sha", sa.Text()),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("project_id", "number", name="uq_project_versions_number"),
        sa.CheckConstraint("number > 0", name="ck_project_versions_positive_number"),
        sa.CheckConstraint(
            "status IN ('queued','running','ready','failed','cancelled','unchanged')",
            name="ck_project_versions_status",
        ),
    )
    # IDs derive from existing durable rows; ordering has a UUID tie breaker.
    # Legacy thumbnails are deliberately not promoted without commit provenance.
    op.execute(
        sa.text("""
        WITH entries AS (
          SELECT r.id, r.project_id, r.user_message_id AS source_message_id,
                 r.id AS generation_run_id, COALESCE(s.id, base.id) AS snapshot_id,
                 base.id AS base_snapshot_id, COALESCE(s.commit_sha, base.commit_sha) AS commit_sha,
                 COALESCE(u.content, s.prompt_text, '') AS prompt_text, a.model_id,
                 CASE r.status WHEN 'completed' THEN CASE WHEN s.id IS NULL THEN 'unchanged' ELSE 'ready' END
                   WHEN 'failed' THEN 'failed' WHEN 'cancelled' THEN 'cancelled'
                   WHEN 'running' THEN 'running' WHEN 'cancel_requested' THEN 'running'
                   ELSE 'queued' END AS status, r.created_at
          FROM generation_runs r
          LEFT JOIN messages u ON u.id = r.user_message_id AND u.project_id = r.project_id
          LEFT JOIN messages a ON a.id = r.assistant_message_id AND a.project_id = r.project_id
          LEFT JOIN snapshots s ON s.id::text = COALESCE(a.snapshot_id::text, r.agent_state->>'snapshot_id')
            AND s.project_id = r.project_id
          LEFT JOIN LATERAL (
            SELECT b.id, b.commit_sha FROM snapshots b
            WHERE b.project_id = r.project_id AND (
              (COALESCE(r.agent_state->'dispatch', '{}'::jsonb) ? 'current_snapshot_id'
                AND b.id::text = r.agent_state->'dispatch'->>'current_snapshot_id')
              OR (NOT (COALESCE(r.agent_state->'dispatch', '{}'::jsonb) ? 'current_snapshot_id')
                AND b.created_at <= r.created_at)
            )
            ORDER BY b.created_at DESC, b.id DESC LIMIT 1
          ) base ON TRUE
          UNION ALL
          SELECT s.id, s.project_id,
                 NULL::uuid, NULL::uuid, s.id, s.parent_id, s.commit_sha,
                 s.prompt_text, s.model_id, 'ready', s.created_at
          FROM snapshots s
          WHERE NULLIF(trim(s.prompt_text), '') IS NOT NULL
            AND NOT EXISTS (
              SELECT 1 FROM generation_runs r LEFT JOIN messages a ON a.id = r.assistant_message_id
              WHERE (a.snapshot_id = s.id OR r.agent_state->>'snapshot_id' = s.id::text)
                AND r.project_id = s.project_id
            )
        )
        INSERT INTO project_versions
          (id, project_id, number, source_message_id, generation_run_id, snapshot_id,
           base_snapshot_id, commit_sha, prompt_text, model_id, status, created_at)
        SELECT id, project_id, row_number() OVER (PARTITION BY project_id ORDER BY created_at, id),
               source_message_id, generation_run_id, snapshot_id, base_snapshot_id,
               commit_sha, prompt_text, model_id, status, created_at FROM entries
    """)
    )


def downgrade() -> None:
    op.drop_table("project_versions")
    op.drop_column("snapshots", "preview_commit_sha")
    op.drop_column("snapshots", "preview_status")
    op.drop_column("snapshots", "preview_manifest")
