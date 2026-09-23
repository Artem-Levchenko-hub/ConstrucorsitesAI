"""Account usage journal and Free v2 with the owner's entitlement model.

1. ``billing_usage_events`` — per-account journal for spend that has no other
   trace in the platform DB. Today that is publications: the usage report and
   the publish-slot entitlement read them here instead of asking the
   orchestrator's host journal.
2. Free v2. Free v1 (migration 0035) promised one project, no publications and
   no integrations, but the owner's model of 2026-09-17 is the opposite: Free
   gives full access to building and publishing MAX apps, the only quotas are
   generations and the app's visitor traffic, and a Free app merely sleeps when
   idle. Plan terms are immutable rows (trigger ``billing_plans_protect_terms``),
   so the model ships as a new version; every live Free subscription moves to
   it and v1 stays inactive for history. Ended subscriptions keep pointing at
   the version they had.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0070_billing_usage_events"
down_revision: str | None = "0069_retire_business_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FREE_V1 = "00000000-0000-4000-8000-000000000001"
FREE_V2 = "00000000-0000-4000-8000-000000000004"
LIVE_STATUSES = "('pending_payment', 'trialing', 'active', 'past_due', 'paused')"


def upgrade() -> None:
    op.create_table(
        "billing_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("billing_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cost_rub", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('publication')", name="ck_billing_usage_events_kind"),
        sa.CheckConstraint("quantity > 0", name="ck_billing_usage_events_quantity"),
        sa.CheckConstraint("cost_rub >= 0", name="ck_billing_usage_events_cost"),
        sa.ForeignKeyConstraint(
            ["billing_account_id"],
            ["billing_accounts.id"],
            name=op.f("fk_billing_usage_events_billing_account_id_billing_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_billing_usage_events_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_billing_usage_events_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_usage_events")),
        sa.UniqueConstraint("external_ref", name=op.f("uq_billing_usage_events_external_ref")),
    )
    op.create_index(
        "ix_billing_usage_events_account_kind_created",
        "billing_usage_events",
        ["billing_account_id", "kind", "created_at"],
    )
    op.create_index(
        "ix_billing_usage_events_project_kind",
        "billing_usage_events",
        ["project_id", "kind"],
    )

    # Free v2 is inserted inactive, v1 is switched off, then v2 is switched on:
    # `uq_billing_plans_active_code` allows one active row per code at a time
    # and the terms trigger permits changing nothing but `is_active`.
    op.execute(
        f"""
        INSERT INTO billing_plans
            (id, code, version, name, price_rub, billing_interval, included_credit_rub,
             entitlements, sort_order, is_active)
        VALUES
            ('{FREE_V2}'::uuid, 'free', 2, 'Free', 0.00, 'month', 0.0000,
             '{{"max_projects": null, "static_publish_slots": null, "always_on_slots": 0,
               "team_seats": 1, "custom_domains": 0, "integrations": true,
               "preview_idle_minutes": 15}}'::jsonb,
             0, false)
        ON CONFLICT (code, version) DO NOTHING
        """
    )
    op.execute(f"UPDATE billing_plans SET is_active = false WHERE id = '{FREE_V1}'::uuid")
    op.execute(f"UPDATE billing_plans SET is_active = true WHERE id = '{FREE_V2}'::uuid")
    op.execute(
        f"""
        UPDATE subscriptions
           SET plan_id = '{FREE_V2}'::uuid, updated_at = now()
         WHERE plan_id = '{FREE_V1}'::uuid AND status IN {LIVE_STATUSES}
        """
    )


def downgrade() -> None:
    # Every subscription must leave v2 before the row can go (FK RESTRICT).
    op.execute(
        f"""
        UPDATE subscriptions
           SET plan_id = '{FREE_V1}'::uuid, updated_at = now()
         WHERE plan_id = '{FREE_V2}'::uuid
        """
    )
    op.execute(f"UPDATE billing_plans SET is_active = false WHERE id = '{FREE_V2}'::uuid")
    op.execute(f"UPDATE billing_plans SET is_active = true WHERE id = '{FREE_V1}'::uuid")
    op.execute(f"DELETE FROM billing_plans WHERE id = '{FREE_V2}'::uuid")

    op.drop_index("ix_billing_usage_events_project_kind", table_name="billing_usage_events")
    op.drop_index("ix_billing_usage_events_account_kind_created", table_name="billing_usage_events")
    op.drop_table("billing_usage_events")
