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
    id          uuid          PRIMARY KEY,
    user_id     uuid          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id  uuid          NULL REFERENCES messages(id) ON DELETE SET NULL,
    amount_rub  numeric(12,4) NOT NULL,
    description text          NOT NULL,
    created_at  timestamptz   NOT NULL DEFAULT now()
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
