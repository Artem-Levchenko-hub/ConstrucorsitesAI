"""MAX account verification, business identities and provider payments.

Revision ID: 0030_max_accounts_payments
Revises: 0029_max_project_configs
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_max_accounts_payments"
down_revision = "0029_max_project_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.add_column(
        "users", sa.Column("session_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("users", sa.Column("status", sa.Text(), nullable=False, server_default="active"))
    op.add_column("users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("delete_after", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_users_status", "users", "status IN ('active', 'suspended', 'deletion_pending')"
    )
    # Existing named accounts have already been using the product. Grandfather
    # their email so this release cannot lock an owner out of an active project.
    op.execute(
        "UPDATE users SET email_verified_at = created_at "
        "WHERE is_anon = false AND email IS NOT NULL"
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_agent", sa.Text()),
        sa.Column("ip_address", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index("ix_auth_sessions_user_active", "auth_sessions", ["user_id", "revoked_at"])

    op.create_table(
        "auth_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')",
            name="ck_auth_tokens_purpose",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_tokens_token_hash")),
    )
    op.create_index("ix_auth_tokens_user_purpose", "auth_tokens", ["user_id", "purpose"])

    op.create_table(
        "legal_acceptances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("document_version", sa.Text(), nullable=False),
        sa.Column("ip_address", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "document_type IN ('terms', 'privacy', 'personal_data', 'marketing')",
            name="ck_legal_acceptances_document_type",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_acceptances")),
    )
    op.create_index(
        "ix_legal_acceptances_user_created",
        "legal_acceptances",
        ["user_id", "accepted_at"],
    )

    op.create_table(
        "business_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("inn", sa.Text(), nullable=False),
        sa.Column("ogrn", sa.Text()),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("verification_source", sa.Text()),
        sa.Column("verification_note", sa.Text()),
        sa.Column(
            "verification_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('legal_entity', 'sole_proprietor', 'self_employed')",
            name="ck_business_profiles_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'rejected', 'suspended')",
            name="ck_business_profiles_status",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_profiles")),
        sa.UniqueConstraint("inn", name=op.f("uq_business_profiles_inn")),
    )
    op.create_table(
        "business_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="owner"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_business_members_role"),
        sa.ForeignKeyConstraint(["business_id"], ["business_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_members")),
        sa.UniqueConstraint("business_id", "user_id", name="uq_business_members_pair"),
        sa.UniqueConstraint("user_id", name="uq_business_members_user"),
    )
    op.create_index("ix_business_members_business", "business_members", ["business_id"])
    op.create_table(
        "business_entitlements",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("free_generation_limit", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("free_generations_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["business_id"], ["business_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("business_id", name=op.f("pk_business_entitlements")),
    )

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default="yookassa"),
        sa.Column("provider_payment_id", sa.Text()),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("package_code", sa.Text(), nullable=False),
        sa.Column("amount_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("credit_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("confirmation_url", sa.Text()),
        sa.Column(
            "provider_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("refunded_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'waiting_for_capture', 'succeeded', 'cancelled', "
            "'refunded', 'failed')",
            name="ck_payments_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_payments_idempotency_key")),
        sa.UniqueConstraint("provider_payment_id", name=op.f("uq_payments_provider_payment_id")),
    )
    op.create_index("ix_payments_user_created", "payments", ["user_id", "created_at"])
    op.create_table(
        "wallet_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_type", sa.Text(), nullable=False),
        sa.Column("amount_rub", sa.Numeric(12, 4), nullable=False),
        sa.Column("balance_after_rub", sa.Numeric(12, 4), nullable=False),
        sa.Column("external_ref", sa.Text()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "entry_type IN ('payment', 'refund', 'usage', 'adjustment')",
            name="ck_wallet_ledger_entry_type",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallet_ledger_entries")),
        sa.UniqueConstraint("external_ref", name=op.f("uq_wallet_ledger_entries_external_ref")),
    )
    op.create_index(
        "ix_wallet_ledger_user_created",
        "wallet_ledger_entries",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("wallet_ledger_entries")
    op.drop_table("payments")
    op.drop_table("business_entitlements")
    op.drop_table("business_members")
    op.drop_table("business_profiles")
    op.drop_table("legal_acceptances")
    op.drop_table("auth_tokens")
    op.drop_table("auth_sessions")
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.drop_column("users", "delete_after")
    op.drop_column("users", "deletion_requested_at")
    op.drop_column("users", "status")
    op.drop_column("users", "session_version")
    op.drop_column("users", "email_verified_at")
