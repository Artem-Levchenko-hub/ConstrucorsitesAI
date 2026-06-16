// Idempotent schema bootstrap for the nextjs-entities stack.
//
// WHY not `drizzle-kit push`: push introspects the database first ("Pulling
// schema from database…"), and on the shared per-project Postgres that step
// hangs for minutes, which (run at boot) would block the dev server from ever
// coming up. Our schema is FIXED (Auth.js tables + the generic `records`
// store), so we create it with plain `CREATE TABLE IF NOT EXISTS` — instant,
// deterministic, no introspection. Uses the `pg` driver that's already a
// dependency. The connection's search_path (set by the orchestrator DSN) points
// at the project's schema, so the unqualified DDL lands there.
//
// Keep this in sync with src/lib/db/schema.ts (the column names must match what
// Auth.js and the engine expect). Both are fixed and owned by the template.

import pg from "pg";

const { Pool } = pg;

const DDL = `
CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text,
  email text NOT NULL UNIQUE,
  email_verified timestamptz,
  image text,
  password_hash text,
  role text NOT NULL DEFAULT 'user',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS accounts (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type text NOT NULL,
  provider text NOT NULL,
  provider_account_id text NOT NULL,
  refresh_token text,
  access_token text,
  expires_at integer,
  token_type text,
  scope text,
  id_token text,
  session_state text,
  PRIMARY KEY (provider, provider_account_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  session_token text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_tokens (
  identifier text NOT NULL,
  token text NOT NULL,
  expires timestamptz NOT NULL,
  PRIMARY KEY (identifier, token)
);

CREATE TABLE IF NOT EXISTS records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity text NOT NULL,
  data jsonb NOT NULL,
  created_by uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS records_entity_owner_created_idx
  ON records (entity, created_by, created_at);

-- Crash-proof timestamptz cast for sorting JSONB date fields. A date field's
-- write validator only checks JS Date.parse (lenient), but sorting casts the
-- stored text to timestamptz with Postgres' stricter parser — so one bad-but-
-- JS-valid value ("2025", "June 2025", "2025-02-30") would throw and 500 the
-- whole list. This returns NULL on any cast error instead of erroring, so a
-- single malformed row can never deny the entire view (see engine.ts fieldExpr).
CREATE OR REPLACE FUNCTION safe_to_timestamptz(t text) RETURNS timestamptz
  LANGUAGE plpgsql STABLE AS $fn$
  BEGIN
    RETURN t::timestamptz;
  EXCEPTION WHEN others THEN
    RETURN NULL;
  END;
  $fn$;
`;

const url = process.env.DATABASE_URL;
if (!url) {
  console.error("[init-db] DATABASE_URL is not set — skipping (pages will error on DB access)");
  process.exit(0);
}

const pool = new Pool({ connectionString: url, max: 1, connectionTimeoutMillis: 15000 });

try {
  await pool.query(DDL);
  console.log("[init-db] schema ready (auth tables + records)");
  process.exit(0);
} catch (err) {
  console.error("[init-db] failed:", err?.message ?? err);
  // Non-fatal: let the dev server start anyway.
  process.exit(0);
} finally {
  await pool.end().catch(() => {});
}
