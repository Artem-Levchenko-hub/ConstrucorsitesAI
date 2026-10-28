"""Append-only administrative unit debits and their explicit reversals."""

from alembic import op

revision = "0100_credit_adjustments"
down_revision = "0099_pro_payment_check_price"
branch_labels = None
depends_on = None

# Frozen metadata: never import current application models into a migration.
_CREATE_TABLE = """
CREATE TABLE generation_credit_adjustments (
    id UUID NOT NULL,
    billing_account_id UUID NOT NULL,
    user_id UUID NOT NULL,
    actor_user_id UUID NOT NULL,
    reason TEXT NOT NULL,
    allocations JSONB NOT NULL,
    reverses_adjustment_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_generation_credit_adjustments PRIMARY KEY (id),
    CONSTRAINT ck_generation_credit_adjustments_reason CHECK (reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND 500),
    CONSTRAINT ck_generation_credit_adjustments_allocations CHECK (jsonb_typeof(allocations)='array'),
    CONSTRAINT ck_generation_credit_adjustments_reversal_allocations CHECK (reverses_adjustment_id IS NULL OR allocations='[]'::jsonb),
    CONSTRAINT fk_generation_credit_adjustments_billing_account_id_bil_161a FOREIGN KEY(billing_account_id) REFERENCES billing_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_generation_credit_adjustments_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_generation_credit_adjustments_actor_user_id_users FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT uq_generation_credit_adjustments_reverses_adjustment_id UNIQUE (reverses_adjustment_id),
    CONSTRAINT fk_generation_credit_adjustments_reverses_adjustment_id_177d FOREIGN KEY(reverses_adjustment_id) REFERENCES generation_credit_adjustments (id) ON DELETE RESTRICT
)
"""


def upgrade() -> None:
    op.execute(_CREATE_TABLE)
    op.execute("""
        CREATE INDEX ix_generation_credit_adjustments_billing_account_id
        ON generation_credit_adjustments (billing_account_id)
    """)
    op.execute("""
        CREATE FUNCTION reject_generation_credit_adjustment_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'generation_credit_adjustments_append_only' USING ERRCODE='55000';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER generation_credit_adjustments_append_only
        BEFORE UPDATE OR DELETE ON generation_credit_adjustments
        FOR EACH ROW EXECUTE FUNCTION reject_generation_credit_adjustment_mutation()
    """)


def downgrade() -> None:
    op.execute("LOCK TABLE generation_credit_adjustments IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM generation_credit_adjustments) THEN
                RAISE EXCEPTION 'generation_credit_adjustment_evidence_present';
            END IF;
        END;
        $$
    """)
    op.execute("DROP TABLE generation_credit_adjustments")
    op.execute("DROP FUNCTION reject_generation_credit_adjustment_mutation()")
