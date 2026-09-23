"""Retire business profiles: an account is an email and a password.

MAX verifies the business behind a bot itself, so the platform stops storing
ИНН, ОГРН and legal names. Integration connections and billing accounts are
re-keyed to the owning user, and the free-generation allowance already spent
by a business counts against its owner.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069_retire_business_profiles"
down_revision: str | None = "0068_project_cell_orchestrator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Integration connections belong to the user who owned the business.
    op.add_column(
        "app_integrations",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE app_integrations AS ai
           SET user_id = bm.user_id
          FROM business_members AS bm
         WHERE bm.business_id = ai.business_id AND bm.role = 'owner'
        """
    )
    op.execute("UPDATE app_integrations SET user_id = created_by_user_id WHERE user_id IS NULL")
    op.alter_column("app_integrations", "user_id", nullable=False)
    op.drop_constraint(
        "uq_app_integrations_business_provider", "app_integrations", type_="unique"
    )
    op.drop_index("ix_app_integrations_business_id", table_name="app_integrations")
    op.drop_column("app_integrations", "business_id")
    op.create_foreign_key(
        "fk_app_integrations_user_id_users",
        "app_integrations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_app_integrations_user_provider", "app_integrations", ["user_id", "provider"]
    )
    op.create_index("ix_app_integrations_user_id", "app_integrations", ["user_id"])

    # 2. OAuth handshakes were already keyed by the user.
    op.drop_column("integration_oauth_states", "business_id")

    # 3. A shared business billing account becomes its owner's personal account
    #    again; the wallet, ledger and subscription stay on the same row.
    op.execute(
        """
        UPDATE billing_accounts AS ba
           SET scope = 'personal', personal_user_id = bm.user_id, business_id = NULL
          FROM business_members AS bm
         WHERE ba.scope = 'business'
           AND bm.business_id = ba.business_id
           AND bm.role = 'owner'
           AND NOT EXISTS (
               SELECT 1 FROM billing_accounts AS mine WHERE mine.personal_user_id = bm.user_id
           )
        """
    )
    remaining = op.get_bind().execute(
        sa.text("SELECT count(*) FROM billing_accounts WHERE scope = 'business'")
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"{remaining} business billing account(s) have no owner to return to; "
            "resolve them by hand before retiring business profiles"
        )
    op.drop_index("uq_billing_accounts_business", table_name="billing_accounts")
    op.drop_constraint("ck_billing_accounts_owner", "billing_accounts", type_="check")
    op.drop_constraint("ck_billing_accounts_scope", "billing_accounts", type_="check")
    op.drop_column("billing_accounts", "business_id")
    op.create_check_constraint(
        "ck_billing_accounts_scope", "billing_accounts", "scope = 'personal'"
    )
    op.create_check_constraint(
        "ck_billing_accounts_owner",
        "billing_accounts",
        "scope = 'personal' AND personal_user_id IS NOT NULL",
    )

    # 4. Free builds already spent by the business count against its owner.
    op.execute(
        """
        UPDATE users AS u
           SET free_generations_used = GREATEST(u.free_generations_used, be.free_generations_used)
          FROM business_members AS bm
          JOIN business_entitlements AS be ON be.business_id = bm.business_id
         WHERE bm.user_id = u.id
        """
    )

    # 5. The identity tables go away with everything they held.
    op.drop_table("business_entitlements")
    op.drop_table("business_members")
    op.drop_table("business_profiles")


def downgrade() -> None:
    """Restore the schema only; the deleted identities are not recoverable."""
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

    op.drop_constraint("ck_billing_accounts_owner", "billing_accounts", type_="check")
    op.drop_constraint("ck_billing_accounts_scope", "billing_accounts", type_="check")
    op.add_column(
        "billing_accounts",
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("business_profiles.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_billing_accounts_scope", "billing_accounts", "scope IN ('personal', 'business')"
    )
    op.create_check_constraint(
        "ck_billing_accounts_owner",
        "billing_accounts",
        "(scope = 'personal' AND personal_user_id IS NOT NULL AND business_id IS NULL) "
        "OR (scope = 'business' AND personal_user_id IS NULL AND business_id IS NOT NULL)",
    )
    op.create_index(
        "uq_billing_accounts_business",
        "billing_accounts",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text("business_id IS NOT NULL"),
    )

    # Handshakes and connections cannot be re-attached to a business that no
    # longer exists, so the restored columns stay nullable.
    op.add_column(
        "integration_oauth_states",
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("business_profiles.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.drop_index("ix_app_integrations_user_id", table_name="app_integrations")
    op.drop_constraint("uq_app_integrations_user_provider", "app_integrations", type_="unique")
    op.drop_constraint("fk_app_integrations_user_id_users", "app_integrations", type_="foreignkey")
    op.drop_column("app_integrations", "user_id")
    op.add_column(
        "app_integrations",
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("business_profiles.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_app_integrations_business_provider", "app_integrations", ["business_id", "provider"]
    )
    op.create_index("ix_app_integrations_business_id", "app_integrations", ["business_id"])
