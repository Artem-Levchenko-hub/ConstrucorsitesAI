"""Append-only provider statement confirmation, separate from customer billing."""

import re

import sqlalchemy as sa
from alembic import op

revision = "0080_provider_ledger"
down_revision = "0079_provider_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_calls", sa.Column("provider_organization_id", sa.Text()))
    statements = """
        CREATE TABLE provider_ledger_entries (
            organization_id text NOT NULL,
            operation_id text NOT NULL,
            ref_id text,
            kind text NOT NULL CHECK (kind IN ('usage','correction','other')),
            delta_kopecks bigint NOT NULL CHECK (delta_kopecks > -9223372036854775808),
            currency text NOT NULL CHECK (currency = 'RUB'),
            receipt_hash text NOT NULL CHECK (receipt_hash ~ '^[a-f0-9]{64}$'),
            source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
            source_kind text NOT NULL CHECK (
                source_kind IN ('balance_ledger_export','authorized_ledger_api')),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (organization_id, operation_id)
        );
        CREATE INDEX ix_provider_ledger_ref ON provider_ledger_entries (organization_id,ref_id);
        CREATE TABLE provider_ledger_conflicts (
            organization_id text NOT NULL,
            operation_id text NOT NULL,
            observed_hash text NOT NULL CHECK (observed_hash ~ '^[a-f0-9]{64}$'),
            observed_ref_id text,
            observed_kind text NOT NULL,
            observed_delta_kopecks bigint NOT NULL,
            source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (organization_id, operation_id, observed_hash),
            FOREIGN KEY (organization_id,operation_id)
                REFERENCES provider_ledger_entries (organization_id,operation_id)
        );
        CREATE TABLE provider_ledger_confirmations (
            organization_id text NOT NULL,
            operation_id text NOT NULL,
            call_id uuid NOT NULL REFERENCES provider_calls (id),
            entry_receipt_hash text NOT NULL,
            call_receipt_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (organization_id,operation_id),
            FOREIGN KEY (organization_id,operation_id)
                REFERENCES provider_ledger_entries (organization_id,operation_id)
        );
        CREATE FUNCTION deny_provider_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'immutable_provider_ledger'; END $$;
        CREATE FUNCTION guard_provider_call_organization() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.provider_organization_id IS DISTINCT FROM OLD.provider_organization_id THEN
                RAISE EXCEPTION 'immutable_provider_call_organization';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER provider_call_organization_immutable
            BEFORE UPDATE OF provider_organization_id ON provider_calls
            FOR EACH ROW EXECUTE FUNCTION guard_provider_call_organization();
        CREATE FUNCTION guard_provider_ledger_confirmation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM provider_ledger_entries e JOIN provider_calls c
                    ON c.id=NEW.call_id AND c.provider_scope='llmgw'
                    AND c.provider_organization_id=e.organization_id
                    AND c.provider_request_id=e.ref_id
                WHERE e.organization_id=NEW.organization_id AND e.operation_id=NEW.operation_id
                    AND e.receipt_hash=NEW.entry_receipt_hash
                    AND c.receipt_hash=NEW.call_receipt_hash AND c.status<>'started'
                    AND e.kind IN ('usage','correction')
                    AND (e.kind<>'usage' OR e.delta_kopecks<=0)
            ) THEN
                RAISE EXCEPTION 'provider_ledger_confirmation_scope_mismatch';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER provider_ledger_confirmation_scope
            BEFORE INSERT ON provider_ledger_confirmations FOR EACH ROW
            EXECUTE FUNCTION guard_provider_ledger_confirmation();
    """
    # asyncpg prepares one statement at a time; function bodies keep their semicolons.
    for statement in re.split(r"(?m)^(?=        CREATE )", statements):
        if statement.strip():
            op.execute(statement)
    for table in (
        "provider_ledger_entries",
        "provider_ledger_conflicts",
        "provider_ledger_confirmations",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION deny_provider_ledger_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION deny_provider_ledger_mutation()"
        )


def downgrade() -> None:
    for table in (
        "provider_ledger_confirmations",
        "provider_ledger_conflicts",
        "provider_ledger_entries",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION guard_provider_ledger_confirmation()")
    op.execute("DROP TRIGGER provider_call_organization_immutable ON provider_calls")
    op.execute("DROP FUNCTION guard_provider_call_organization()")
    op.execute("DROP FUNCTION deny_provider_ledger_mutation()")
    op.drop_column("provider_calls", "provider_organization_id")
