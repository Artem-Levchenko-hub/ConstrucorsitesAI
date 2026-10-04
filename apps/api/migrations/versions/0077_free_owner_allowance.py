"""Owner-wide lifetime Free human-message and publication reservations.

Immutable plan rows, paid subscriptions, existing projects and active runs are
unchanged. Backfill only current Free owners from surviving durable evidence.
Deleted failed runs/messages without a completed-build counter cannot be inferred.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0077_free_owner_allowance"
down_revision = "0076_max_analytics"
branch_labels = None
depends_on = None

# The same latest live personal subscription rule as load_plan_context.
CURRENT_FREE = """
SELECT account.personal_user_id
FROM billing_accounts account
JOIN LATERAL (
    SELECT plan.code
    FROM subscriptions subscription
    JOIN billing_plans plan ON plan.id = subscription.plan_id
    WHERE subscription.billing_account_id = account.id
      AND subscription.status IN ('trialing','active','past_due','paused')
    ORDER BY subscription.created_at DESC
    LIMIT 1
) current_plan ON current_plan.code = 'free'
"""


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("free_chat_messages_used", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        op.f("ck_users_free_chat_messages_used"), "users", "free_chat_messages_used IN (0, 1)"
    )
    # Deliberately no cascading FK: deletion or an unknown result cannot reopen
    # the allowance. A zero UUID conservatively seals a deleted historical slot.
    op.add_column("users", sa.Column("free_publication_project_id", pg.UUID(as_uuid=True)))
    op.execute(
        sa.text(f"""
        UPDATE users owner SET free_chat_messages_used = 1
        WHERE owner.id IN ({CURRENT_FREE}) AND (
            owner.free_generations_used > 0
            OR EXISTS (SELECT 1 FROM generation_runs run WHERE run.user_id = owner.id)
            OR EXISTS (
                SELECT 1 FROM messages message JOIN projects project
                  ON project.id = message.project_id
                WHERE project.owner_id = owner.id AND message.role = 'user'
            )
        )
    """)
    )
    op.execute(
        sa.text(f"""
        UPDATE users owner SET free_publication_project_id = COALESCE(
            (SELECT event.project_id FROM billing_usage_events event
             WHERE event.user_id = owner.id AND event.kind = 'publication'
             ORDER BY event.created_at, event.id LIMIT 1),
            '00000000-0000-0000-0000-000000000000'::uuid
        )
        WHERE owner.id IN ({CURRENT_FREE}) AND EXISTS (
            SELECT 1 FROM billing_usage_events event
            WHERE event.user_id = owner.id AND event.kind = 'publication'
        )
    """)
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_free_chat_messages_used"), "users", type_="check")
    op.drop_column("users", "free_publication_project_id")
    op.drop_column("users", "free_chat_messages_used")
