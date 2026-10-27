"""Freeze account security mail and personal payment tests; never reprice contracts."""

from alembic import op

revision = "0098_account_security_offers"
down_revision = "0097_public_prices"
branch_labels = None
depends_on = None

# Frozen DDL: no imports of current application models.
_CREATE_SQL = (
    """CREATE TABLE email_change_challenges (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    old_email TEXT NOT NULL,
    new_email TEXT NOT NULL,
    session_version INTEGER NOT NULL,
    old_token_hash TEXT NOT NULL,
    new_token_hash TEXT NOT NULL,
    old_confirmed_at TIMESTAMP WITH TIME ZONE,
    new_confirmed_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_email_change_challenges PRIMARY KEY (id),
    CONSTRAINT ck_email_change_challenges_different_emails CHECK (lower(old_email) <>
        lower(new_email)),
    CONSTRAINT ck_email_change_challenges_different_tokens CHECK (old_token_hash <>
        new_token_hash),
    CONSTRAINT fk_email_change_challenges_user_id_users FOREIGN KEY(user_id) REFERENCES users
        (id) ON DELETE CASCADE,
    CONSTRAINT uq_email_change_challenges_old_token_hash UNIQUE (old_token_hash),
    CONSTRAINT uq_email_change_challenges_new_token_hash UNIQUE (new_token_hash)
)""",
    """CREATE TABLE account_security_emails (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    event_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    status TEXT DEFAULT 'pending' NOT NULL,
    attempts INTEGER DEFAULT '0' NOT NULL,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    claimed_at TIMESTAMP WITH TIME ZONE,
    sent_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_account_security_emails PRIMARY KEY (id),
    CONSTRAINT ck_account_security_emails_kind CHECK (kind IN ('password_changed',
        'password_reset', 'email_changed')),
    CONSTRAINT ck_account_security_emails_status CHECK (status IN ('pending', 'sending', 'sent',
        'uncertain', 'failed')),
    CONSTRAINT ck_account_security_emails_attempts CHECK (attempts >= 0),
    CONSTRAINT fk_account_security_emails_user_id_users FOREIGN KEY(user_id) REFERENCES users
        (id) ON DELETE CASCADE,
    CONSTRAINT uq_account_security_emails_event_key UNIQUE (event_key)
)""",
    """CREATE TABLE billing_purchase_offers (
    id UUID NOT NULL,
    billing_account_id UUID NOT NULL,
    user_id UUID NOT NULL,
    issued_by_user_id UUID NOT NULL,
    amount_rub NUMERIC(12, 2) NOT NULL,
    currency TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reserved_payment_id UUID,
    consumed_payment_id UUID,
    consumed_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_billing_purchase_offers PRIMARY KEY (id),
    CONSTRAINT ck_billing_purchase_offers_one_ruble CHECK (amount_rub = 1.00),
    CONSTRAINT ck_billing_purchase_offers_currency CHECK (currency = 'RUB'),
    CONSTRAINT ck_billing_purchase_offers_expiry CHECK (expires_at > created_at),
    CONSTRAINT ck_billing_purchase_offers_consumption CHECK ((consumed_payment_id IS NULL) =
        (consumed_at IS NULL)),
    CONSTRAINT uq_billing_purchase_offers_billing_account_id UNIQUE (billing_account_id),
    CONSTRAINT fk_billing_purchase_offers_billing_account_id_billing_accounts FOREIGN
        KEY(billing_account_id) REFERENCES billing_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_billing_purchase_offers_user_id_users FOREIGN KEY(user_id) REFERENCES users
        (id) ON DELETE RESTRICT,
    CONSTRAINT fk_billing_purchase_offers_issued_by_user_id_users FOREIGN KEY(issued_by_user_id)
        REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT uq_billing_purchase_offers_reserved_payment_id UNIQUE (reserved_payment_id),
    CONSTRAINT fk_billing_purchase_offers_reserved_payment_id_payments FOREIGN
        KEY(reserved_payment_id) REFERENCES payments (id) ON DELETE RESTRICT,
    CONSTRAINT uq_billing_purchase_offers_consumed_payment_id UNIQUE (consumed_payment_id),
    CONSTRAINT fk_billing_purchase_offers_consumed_payment_id_payments FOREIGN
        KEY(consumed_payment_id) REFERENCES payments (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE billing_offer_payments (
    payment_id UUID NOT NULL,
    offer_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_billing_offer_payments PRIMARY KEY (payment_id),
    CONSTRAINT fk_billing_offer_payments_payment_id_payments FOREIGN KEY(payment_id) REFERENCES
        payments (id) ON DELETE CASCADE,
    CONSTRAINT fk_billing_offer_payments_offer_id_billing_purchase_offers FOREIGN KEY(offer_id)
        REFERENCES billing_purchase_offers (id) ON DELETE RESTRICT
)""",
)

_INDEX_SQL = (
    """CREATE INDEX ix_email_change_user_active ON email_change_challenges (user_id, used_at)""",
    """CREATE INDEX ix_account_security_email_ready ON account_security_emails (status,
available_at)""",
    """CREATE INDEX ix_billing_offer_payments_offer_id ON billing_offer_payments (offer_id)""",
)

_PAYMENT_PURPOSE = (
    "purpose IN ('wallet_topup', 'subscription_initial', 'subscription_renewal', 'payment_test')"
)

_PAYMENT_LINK = (
    "(purpose IN ('wallet_topup', 'payment_test') AND subscription_id IS NULL) "
    "OR (purpose IN ('subscription_initial', 'subscription_renewal') AND "
    "subscription_id IS NOT NULL)"
)

_NOTICE_TYPES = "event_type IN ('paid', 'ending_3', 'ending_1', 'payment_test')"

_NOTICE_SHAPE = (
    "(event_type = 'payment_test' AND subscription_id IS NULL AND period_end IS "
    "NULL) OR (event_type <> 'payment_test' AND subscription_id IS NOT NULL AND "
    "period_end IS NOT NULL)"
)

_OLD_PAYMENT_PURPOSE = "purpose IN ('wallet_topup', 'subscription_initial', 'subscription_renewal')"

_OLD_PAYMENT_LINK = (
    "(purpose = 'wallet_topup' AND subscription_id IS NULL) OR (purpose IN "
    "('subscription_initial', 'subscription_renewal') AND subscription_id IS NOT "
    "NULL)"
)

_OLD_NOTICE_TYPES = "event_type IN ('paid', 'ending_3', 'ending_1')"


def _replace_check(table: str, name: str, expression: str) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, expression)


def upgrade() -> None:
    for statement in (*_CREATE_SQL, *_INDEX_SQL):
        op.execute(statement)
    _replace_check("payments", "ck_payments_ck_payments_purpose", _PAYMENT_PURPOSE)
    _replace_check("payments", "ck_payments_ck_payments_subscription_link", _PAYMENT_LINK)
    _replace_check("email_notifications", "ck_email_notifications_event_type", _NOTICE_TYPES)
    op.execute("ALTER TABLE email_notifications ALTER COLUMN subscription_id DROP NOT NULL")
    op.execute("ALTER TABLE email_notifications ALTER COLUMN period_end DROP NOT NULL")
    op.create_check_constraint(
        op.f("ck_email_notifications_subscription_shape"), "email_notifications", _NOTICE_SHAPE
    )
    # Only the verified billing worker can settle its already-issued test offer.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'max_billing') THEN
            GRANT SELECT ON TABLE public.billing_purchase_offers TO max_billing;
            GRANT UPDATE (consumed_payment_id, consumed_at)
              ON TABLE public.billing_purchase_offers TO max_billing;
            GRANT SELECT ON TABLE public.billing_offer_payments TO max_billing;
          END IF;
        END $$;
    """)


def downgrade() -> None:
    # No money, completed security event or pending confirmation may disappear.
    op.execute("""
        LOCK TABLE billing_offer_payments, billing_purchase_offers,
          account_security_emails, email_change_challenges,
          payments, email_notifications IN ACCESS EXCLUSIVE MODE
    """)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM billing_offer_payments)
             OR EXISTS (SELECT 1 FROM billing_purchase_offers)
             OR EXISTS (SELECT 1 FROM account_security_emails)
             OR EXISTS (SELECT 1 FROM email_change_challenges)
             OR EXISTS (SELECT 1 FROM payments WHERE purpose = 'payment_test')
             OR EXISTS (SELECT 1 FROM email_notifications
                         WHERE event_type = 'payment_test'
                            OR subscription_id IS NULL OR period_end IS NULL) THEN
            RAISE EXCEPTION 'account_security_offer_evidence_present';
          END IF;
        END $$;
    """)
    op.drop_constraint(
        op.f("ck_email_notifications_subscription_shape"), "email_notifications", type_="check"
    )
    _replace_check("email_notifications", "ck_email_notifications_event_type", _OLD_NOTICE_TYPES)
    op.execute("ALTER TABLE email_notifications ALTER COLUMN subscription_id SET NOT NULL")
    op.execute("ALTER TABLE email_notifications ALTER COLUMN period_end SET NOT NULL")
    _replace_check("payments", "ck_payments_ck_payments_purpose", _OLD_PAYMENT_PURPOSE)
    _replace_check("payments", "ck_payments_ck_payments_subscription_link", _OLD_PAYMENT_LINK)
    for table in (
        "billing_offer_payments",
        "billing_purchase_offers",
        "account_security_emails",
        "email_change_challenges",
    ):
        op.drop_table(table)
