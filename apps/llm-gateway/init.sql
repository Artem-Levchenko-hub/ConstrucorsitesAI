-- init.sql — schema needed for the LLM Gateway to write to Postgres.
--
-- Why it lives here: per AGENT-C-LLM-GATEWAY.md, agent C goes directly to the
-- shared Postgres (variant 1). Until agent B's Alembic migrations land
-- (planned: 0001 users/wallets, 0002 projects/snapshots/messages, 0003
-- wallet_charges/usage), this file is the bootstrap schema so the gateway can
-- run end-to-end locally.
--
-- Idempotent: safe to re-run. Once B's migrations exist, this file becomes
-- redundant — delete it then.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS users (
    id            uuid        PRIMARY KEY,
    email         citext      UNIQUE NOT NULL,
    password_hash text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id                  uuid        PRIMARY KEY,
    owner_id            uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    slug                text        UNIQUE NOT NULL,
    template            text        NOT NULL CHECK (template IN ('blank','landing','portfolio','blog')),
    current_snapshot_id uuid        NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id          uuid        PRIMARY KEY,
    project_id  uuid        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    snapshot_id uuid        NULL,
    role        text        NOT NULL CHECK (role IN ('user','assistant','system')),
    content     text        NOT NULL,
    model_id    text        NULL,
    tokens_in   integer     NULL,
    tokens_out  integer     NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallets (
    user_id     uuid          PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    balance_rub numeric(12,4) NOT NULL DEFAULT 100.0000,
    updated_at  timestamptz   NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallet_charges (
    id                uuid          PRIMARY KEY,
    user_id           uuid          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id        uuid          NULL REFERENCES messages(id) ON DELETE SET NULL,
    subscription_id   uuid          NULL,
    entry_type        text          NOT NULL DEFAULT 'usage'
        CHECK (entry_type IN (
            'usage','topup','payment','refund','subscription_credit','adjustment'
        )),
    amount_rub        numeric(12,4) NOT NULL,
    balance_after_rub numeric(12,4) NOT NULL,
    external_ref      text          UNIQUE NULL,
    description       text          NOT NULL,
    created_at        timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wallet_charges_user_created_idx
    ON wallet_charges(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS usage (
    id          uuid          PRIMARY KEY,
    user_id     uuid          NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  uuid          NULL REFERENCES projects(id) ON DELETE SET NULL,
    message_id  uuid          NULL REFERENCES messages(id) ON DELETE SET NULL,
    model_id    text          NOT NULL,
    tokens_in   integer       NOT NULL,
    tokens_out  integer       NOT NULL,
    cost_rub    numeric(12,4) NOT NULL,
    created_at  timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS usage_user_created_idx  ON usage(user_id,  created_at DESC);
CREATE INDEX IF NOT EXISTS usage_model_created_idx ON usage(model_id, created_at DESC);

-- Provider expenses are independent of customer billing (Alembic 0079).
-- Attribution UUIDs intentionally have no cascading foreign keys: deleting a
-- user/project must not delete the evidence of money spent with the provider.
CREATE TABLE IF NOT EXISTS provider_calls (
    id                  uuid          PRIMARY KEY,
    provider_scope      text          NOT NULL,
    route               text          NOT NULL,
    requested_model     text          NOT NULL,
    actual_model        text          NULL,
    expense_owner       text          NOT NULL,
    user_id             uuid          NULL,
    project_id          uuid          NULL,
    run_id              uuid          NULL,
    message_id          uuid          NULL,
    usage_id            uuid          NULL,
    stage               text          NOT NULL,
    free                boolean       NOT NULL,
    status              text          NOT NULL,
    provider_request_id text          NULL,
    tokens_in           bigint        NULL,
    tokens_out          bigint        NULL,
    cache_read_tokens   bigint        NULL,
    cache_write_tokens  bigint        NULL,
    calculated_cost_rub numeric(24,8) NULL,
    provider_cost_rub   numeric(24,8) NULL,
    provider_cost_usd   numeric(24,8) NULL,
    cost_provenance     jsonb         NULL,
    error_type          text          NULL,
    receipt_hash        text          NULL,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    finished_at         timestamptz   NULL,
    CONSTRAINT ck_provider_calls_scope CHECK (provider_scope = 'llmgw'),
    CONSTRAINT ck_provider_calls_status
        CHECK (status IN ('started','completed','failed','ambiguous')),
    CONSTRAINT ck_provider_calls_owner
        CHECK ((expense_owner='user' AND user_id IS NOT NULL) OR
               (expense_owner='platform' AND user_id IS NULL)),
    CONSTRAINT ck_provider_calls_finished
        CHECK ((status='started' AND finished_at IS NULL) OR
               (status<>'started' AND finished_at IS NOT NULL)),
    CONSTRAINT ck_provider_calls_tokens_in
        CHECK (tokens_in IS NULL OR tokens_in >= 0),
    CONSTRAINT ck_provider_calls_tokens_out
        CHECK (tokens_out IS NULL OR tokens_out >= 0),
    CONSTRAINT ck_provider_calls_cache_read_tokens
        CHECK (cache_read_tokens IS NULL OR cache_read_tokens >= 0),
    CONSTRAINT ck_provider_calls_cache_write_tokens
        CHECK (cache_write_tokens IS NULL OR cache_write_tokens >= 0),
    CONSTRAINT ck_provider_calls_calculated_cost_rub
        CHECK (calculated_cost_rub IS NULL OR calculated_cost_rub >= 0),
    CONSTRAINT ck_provider_calls_provider_cost_rub
        CHECK (provider_cost_rub IS NULL OR provider_cost_rub >= 0),
    CONSTRAINT ck_provider_calls_provider_cost_usd
        CHECK (provider_cost_usd IS NULL OR provider_cost_usd >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_calls_receipt
    ON provider_calls(provider_scope, provider_request_id)
    WHERE provider_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_provider_calls_created_at ON provider_calls(created_at);
CREATE INDEX IF NOT EXISTS ix_provider_calls_run_id ON provider_calls(run_id);
