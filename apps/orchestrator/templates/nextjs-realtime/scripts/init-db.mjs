// One-shot, idempotent schema initializer for the realtime template.
//
// Runs before Next boots (see docker-entrypoint.sh). Uses the plain `pg`
// driver with CREATE ... IF NOT EXISTS so it is safe to run on every start
// and needs no introspection. The schema is fixed: Auth.js users plus the
// realtime channels / channel_members / messages store.
//
// On success: closes the pool and exits 0.
// On error:   prints to stderr and exits 1 (entrypoint then fail-softs).

import pg from "pg";
const { Pool } = pg;

// NB: gen_random_uuid() is BUILT IN to Postgres 13+ (we run 16), so we do NOT
// `CREATE EXTENSION pgcrypto` — it needs a CREATE privilege the app DB user may
// lack, and because pg runs this DDL as one batch, that single failing statement
// aborted the WHOLE schema init ("permission denied to create extension
// pgcrypto") and left the app half-built. No extension is needed.
const DDL = `
CREATE TABLE IF NOT EXISTS users ( id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email text NOT NULL UNIQUE, name text, image text, password_hash text, role text NOT NULL DEFAULT 'user', created_at timestamptz NOT NULL DEFAULT now() );
CREATE TABLE IF NOT EXISTS channels ( id text PRIMARY KEY, kind text NOT NULL DEFAULT 'conversation', title text, created_by uuid, created_at timestamptz NOT NULL DEFAULT now() );
CREATE TABLE IF NOT EXISTS channel_members ( id uuid PRIMARY KEY DEFAULT gen_random_uuid(), channel_id text NOT NULL, user_id uuid NOT NULL, role text NOT NULL DEFAULT 'member', created_at timestamptz NOT NULL DEFAULT now(), CONSTRAINT channel_members_channel_user_uniq UNIQUE (channel_id, user_id) );
CREATE INDEX IF NOT EXISTS channel_members_channel_idx ON channel_members (channel_id);
CREATE INDEX IF NOT EXISTS channel_members_user_idx ON channel_members (user_id);
CREATE TABLE IF NOT EXISTS messages ( id uuid PRIMARY KEY DEFAULT gen_random_uuid(), channel_id text NOT NULL, user_id uuid NOT NULL, type text NOT NULL DEFAULT 'message', body text NOT NULL, created_at timestamptz NOT NULL DEFAULT now() );
CREATE INDEX IF NOT EXISTS messages_channel_created_idx ON messages (channel_id, created_at);
`;

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

try {
  // pg's simple query protocol runs all statements in one round-trip.
  await pool.query(DDL);
  console.log("[init-db] schema ensured (users, channels, channel_members, messages)");
} catch (err) {
  console.error("[init-db] failed to ensure schema:", err);
  process.exitCode = 1;
} finally {
  await pool.end();
}
