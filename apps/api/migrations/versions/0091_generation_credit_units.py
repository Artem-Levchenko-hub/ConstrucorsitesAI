"""Integer generation allowances; preserve historical financial records."""

import sqlalchemy as sa
from alembic import op

revision = "0091_generation_credit_units"
down_revision = "0090_aitunnel_text_fallback"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "projects",
        sa.Column(
            "generation_initial_accepted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.execute(
        (
            "UPDATE projects SET generation_initial_accepted=tr"
            "ue WHERE EXISTS(SELECT 1 FROM generation_runs r LE"
            "FT JOIN messages m ON m.id=r.assistant_message_id "
            "LEFT JOIN snapshots s ON s.id=m.snapshot_id WHERE "
            "r.project_id=projects.id AND r.status='completed' "
            "AND r.response_mode IN ('build','edit') AND (s.prompt_text IS "
            "NOT NULL OR r.agent_state->>'billing_accepted_cand"
            "idate' IS NOT NULL OR (r.agent_state->>'snapshot_i"
            "d' IS NOT NULL AND r.agent_state->>'commit_sha' IS"
            " NOT NULL)))"
        )
    )
    op.add_column(
        "payments", sa.Column("edit_units", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "payments", sa.Column("edits_only", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("payments", sa.Column("package_version", sa.Integer(), nullable=True))
    op.execute(
        (
            "\nCREATE TABLE generation_credit_grants (\n\tid UUID "
            "NOT NULL, \n\tbilling_account_id UUID NOT NULL, \n\tbu"
            "cket TEXT NOT NULL, \n\tscope TEXT NOT NULL, \n\tunits"
            " INTEGER NOT NULL, \n\texternal_ref TEXT NOT NULL, \n"
            "\tpayment_id UUID, \n\tsubscription_id UUID, \n\tperiod"
            "_start TIMESTAMP WITH TIME ZONE, \n\tperiod_end TIME"
            "STAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH "
            "TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_"
            "generation_credit_grants PRIMARY KEY (id), \n\tCONST"
            "RAINT ck_generation_credit_grants_units_nonnegativ"
            "e CHECK (units >= 0), \n\tCONSTRAINT ck_generation_c"
            "redit_grants_bucket CHECK (bucket IN ('initial','e"
            "dit','edit_only','flexible')), \n\tCONSTRAINT ck_gen"
            "eration_credit_grants_scope CHECK (scope IN ('free"
            "','period','package')), \n\tCONSTRAINT ck_generation"
            "_credit_grants_source_binding CHECK ((scope='perio"
            "d' AND payment_id IS NOT NULL AND subscription_id "
            "IS NOT NULL AND period_start IS NOT NULL AND perio"
            "d_end IS NOT NULL AND period_end>period_start) OR "
            "(scope='package' AND payment_id IS NOT NULL AND pe"
            "riod_start IS NULL AND period_end IS NULL) OR (sco"
            "pe='free' AND payment_id IS NULL AND period_start "
            "IS NULL AND period_end IS NULL)), \n\tCONSTRAINT fk_"
            "generation_credit_grants_billing_account_id_billin"
            "g_accounts FOREIGN KEY(billing_account_id) REFEREN"
            "CES billing_accounts (id) ON DELETE RESTRICT, \n\tCO"
            "NSTRAINT uq_generation_credit_grants_external_ref "
            "UNIQUE (external_ref), \n\tCONSTRAINT fk_generation_"
            "credit_grants_payment_id_payments FOREIGN KEY(paym"
            "ent_id) REFERENCES payments (id) ON DELETE RESTRIC"
            "T, \n\tCONSTRAINT fk_generation_credit_grants_subscr"
            "iption_id_subscriptions FOREIGN KEY(subscription_i"
            "d) REFERENCES subscriptions (id) ON DELETE RESTRIC"
            "T\n)\n\n"
        )
    )
    op.execute(
        (
            "CREATE INDEX ix_generation_credit_grants_billing_a"
            "ccount_id ON generation_credit_grants (billing_acc"
            "ount_id)"
        )
    )
    op.execute(
        (
            "\nCREATE TABLE generation_credit_reservations (\n\tru"
            "n_id UUID NOT NULL, \n\tbilling_account_id UUID NOT "
            "NULL, \n\tuser_id UUID NOT NULL, \n\tproject_id UUID N"
            "OT NULL, \n\tkind TEXT NOT NULL, \n\tstate TEXT NOT NU"
            "LL, \n\tunits INTEGER NOT NULL, \n\tallocations JSONB "
            "NOT NULL, \n\taccepted_snapshot_id UUID, \n\tcreated_a"
            "t TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,"
            " \n\tsettled_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAI"
            "NT pk_generation_credit_reservations PRIMARY KEY ("
            "run_id), \n\tCONSTRAINT ck_generation_credit_reserva"
            "tions_positive_units CHECK (units > 0), \n\tCONSTRAI"
            "NT ck_generation_credit_reservations_kind CHECK (k"
            "ind IN ('initial','edit')), \n\tCONSTRAINT ck_genera"
            "tion_credit_reservations_state CHECK (state IN ('h"
            "eld','consumed','released')), \n\tCONSTRAINT ck_gene"
            "ration_credit_reservations_allocations CHECK (json"
            "b_typeof(allocations)='array' AND jsonb_array_leng"
            "th(allocations)>0), \n\tCONSTRAINT fk_generation_cre"
            "dit_reservations_billing_account_id_bi_34cf FOREIG"
            "N KEY(billing_account_id) REFERENCES billing_accou"
            "nts (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_gener"
            "ation_credit_reservations_user_id_users FOREIGN KE"
            "Y(user_id) REFERENCES users (id) ON DELETE RESTRIC"
            "T\n)\n\n"
        )
    )
    op.execute(
        (
            "CREATE INDEX ix_generation_credit_reservations_bil"
            "ling_account_id ON generation_credit_reservations "
            "(billing_account_id)"
        )
    )
    op.execute(
        (
            "UPDATE billing_plans SET is_active=false WHERE cod"
            "e IN ('free','pro','business') AND id NOT IN ('000"
            "00000-0000-4000-8000-000000000008','00000000-0000-"
            "4000-8000-000000000009','00000000-0000-4000-8000-0"
            "00000000010')"
        )
    )
    op.create_check_constraint(
        "state_binding",
        "generation_credit_reservations",
        (
            "(state='held' AND accepted_snapshot_id IS NULL AND"
            " settled_at IS NULL) OR (state='consumed' AND acce"
            "pted_snapshot_id IS NOT NULL AND settled_at IS NOT"
            " NULL) OR (state='released' AND accepted_snapshot_"
            "id IS NULL AND settled_at IS NOT NULL)"
        ),
    )
    op.execute(
        sa.text(
            (
                "INSERT INTO billing_plans(id,code,name,version,pri"
                "ce_rub,billing_interval,included_credit_rub,entitl"
                "ements,sort_order,is_active) VALUES(CAST(:id AS uu"
                "id),:code,:name,:version,CAST(:price_rub AS numeri"
                "c),:billing_interval,CAST(:included_credit_rub AS "
                "numeric),CAST(:entitlements AS jsonb),:sort_order,"
                ":is_active) ON CONFLICT(id) DO NOTHING"
            )
        ).bindparams(
            **{
                "id": "00000000-0000-4000-8000-000000000008",
                "code": "free",
                "version": 4,
                "name": "Free",
                "price_rub": "0.00",
                "billing_interval": "month",
                "included_credit_rub": "0.0000",
                "entitlements": (
                    '{"custom_domains": 0, "initial_generations_lifetim'
                    'e": 1, "edits_lifetime": 1, "max_projects": 1, "st'
                    'atic_publish_slots": 1, "always_on_slots": 0, "tea'
                    'm_seats": 1, "integrations": true, "preview_idle_m'
                    'inutes": 15}'
                ),
                "sort_order": 0,
                "is_active": True,
            }
        )
    )
    op.execute(
        sa.text(
            (
                "INSERT INTO billing_plans(id,code,name,version,pri"
                "ce_rub,billing_interval,included_credit_rub,entitl"
                "ements,sort_order,is_active) VALUES(CAST(:id AS uu"
                "id),:code,:name,:version,CAST(:price_rub AS numeri"
                "c),:billing_interval,CAST(:included_credit_rub AS "
                "numeric),CAST(:entitlements AS jsonb),:sort_order,"
                ":is_active) ON CONFLICT(id) DO NOTHING"
            )
        ).bindparams(
            **{
                "id": "00000000-0000-4000-8000-000000000009",
                "code": "pro",
                "version": 3,
                "name": "Pro",
                "price_rub": "1.00",
                "billing_interval": "month",
                "included_credit_rub": "0.0000",
                "entitlements": (
                    '{"custom_domains": 0, "initial_generations_monthly'
                    '": 2, "edits_monthly": 25, "max_projects": 3, "sta'
                    'tic_publish_slots": 3, "always_on_slots": 1, "team'
                    '_seats": 1, "integrations": true, "preview_idle_mi'
                    'nutes": 60}'
                ),
                "sort_order": 10,
                "is_active": True,
            }
        )
    )
    op.execute(
        sa.text(
            (
                "INSERT INTO billing_plans(id,code,name,version,pri"
                "ce_rub,billing_interval,included_credit_rub,entitl"
                "ements,sort_order,is_active) VALUES(CAST(:id AS uu"
                "id),:code,:name,:version,CAST(:price_rub AS numeri"
                "c),:billing_interval,CAST(:included_credit_rub AS "
                "numeric),CAST(:entitlements AS jsonb),:sort_order,"
                ":is_active) ON CONFLICT(id) DO NOTHING"
            )
        ).bindparams(
            **{
                "id": "00000000-0000-4000-8000-000000000010",
                "code": "business",
                "version": 3,
                "name": "Business",
                "price_rub": "6990.00",
                "billing_interval": "month",
                "included_credit_rub": "0.0000",
                "entitlements": (
                    '{"custom_domains": 0, "initial_generations_monthly'
                    '": 5, "edits_monthly": 50, "max_projects": 10, "st'
                    'atic_publish_slots": 10, "always_on_slots": 1, "te'
                    'am_seats": 5, "integrations": true, "preview_idle_'
                    'minutes": 0}'
                ),
                "sort_order": 20,
                "is_active": True,
            }
        )
    )
    op.execute(
        (
            "UPDATE subscriptions SET plan_id='00000000-0000-40"
            "00-8000-000000000008' WHERE plan_id IN (SELECT id "
            "FROM billing_plans WHERE code='free' AND id<>'0000"
            "0000-0000-4000-8000-000000000008') AND status IN ("
            "'pending_payment','trialing','active','past_due','"
            "paused')"
        )
    )


def downgrade():
    guard = (
        "SELECT EXISTS(SELECT 1 FROM generation_credit_gran"
        "ts) OR EXISTS(SELECT 1 FROM generation_credit_rese"
        "rvations) OR EXISTS(SELECT 1 FROM payments WHERE e"
        "dit_units>0 OR package_version IS NOT NULL) OR EXI"
        "STS(SELECT 1 FROM subscriptions WHERE plan_id IN ("
        "'00000000-0000-4000-8000-000000000009','00000000-0"
        "000-4000-8000-000000000010')) OR EXISTS(SELECT 1 F"
        "ROM generation_billing_policies WHERE version='acc"
        "epted-units-v2')"
    )
    if op.get_bind().execute(sa.text(guard)).scalar():
        raise RuntimeError("Financial unit history and quoted contracts cannot be discarded")
    op.execute(
        (
            "UPDATE subscriptions SET plan_id='00000000-0000-40"
            "00-8000-000000000005' WHERE plan_id='00000000-0000"
            "-4000-8000-000000000008'"
        )
    )
    op.execute(
        (
            "UPDATE billing_plans SET is_active=false WHERE id "
            "IN ('00000000-0000-4000-8000-000000000008','000000"
            "00-0000-4000-8000-000000000009','00000000-0000-400"
            "0-8000-000000000010')"
        )
    )
    op.execute(
        (
            "DELETE FROM billing_plans WHERE id IN ('00000000-0"
            "000-4000-8000-000000000008','00000000-0000-4000-80"
            "00-000000000009','00000000-0000-4000-8000-00000000"
            "0010')"
        )
    )
    op.execute(
        (
            "UPDATE billing_plans SET is_active=true WHERE id I"
            "N ('00000000-0000-4000-8000-000000000005','0000000"
            "0-0000-4000-8000-000000000006','00000000-0000-4000"
            "-8000-000000000007')"
        )
    )
    op.drop_table("generation_credit_reservations")
    op.drop_table("generation_credit_grants")
    op.drop_column("projects", "generation_initial_accepted")
    for column in ("package_version", "edits_only", "edit_units"):
        op.drop_column("payments", column)
