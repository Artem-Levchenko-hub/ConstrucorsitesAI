import pg from "pg";

const { Client } = pg;
const schema = process.env.OMNIA_DB_SCHEMA || "public";

if (!/^[a-z_][a-z0-9_]{0,62}$/.test(schema)) {
  throw new Error("OMNIA_DB_SCHEMA is invalid");
}
if (!process.env.DATABASE_URL) {
  throw new Error("DATABASE_URL is required");
}

const qualified = (table) => `"${schema}"."${table}"`;
const users = qualified("max_users");
const foreignKey = (table) => {
  const constraint = `${table}_max_user_id_max_users_max_user_id_fk`;
  return `
DO $$ BEGIN
  ALTER TABLE ${qualified(table)}
    ADD CONSTRAINT "${constraint}"
    FOREIGN KEY ("max_user_id") REFERENCES ${users}("max_user_id")
    ON DELETE CASCADE NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE ${qualified(table)} VALIDATE CONSTRAINT "${constraint}";
EXCEPTION WHEN foreign_key_violation THEN NULL;
END $$`;
};

const statements = [
  `CREATE TABLE IF NOT EXISTS ${users} (
    "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
    "max_user_id" text NOT NULL UNIQUE,
    "first_name" text NOT NULL,
    "last_name" text,
    "username" text,
    "language_code" text,
    "photo_url" text,
    "created_at" timestamptz DEFAULT now() NOT NULL,
    "updated_at" timestamptz DEFAULT now() NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS ${qualified("max_webhook_events")} (
    "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
    "event_key" text NOT NULL UNIQUE,
    "event_type" text NOT NULL,
    "created_at" timestamptz DEFAULT now() NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS ${qualified("max_catalog_items")} (
    "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
    "external_id" text NOT NULL UNIQUE,
    "title" text NOT NULL,
    "description" text DEFAULT '' NOT NULL,
    "price" text DEFAULT '' NOT NULL,
    "action_label" text DEFAULT 'Открыть' NOT NULL,
    "active" boolean DEFAULT true NOT NULL,
    "sort_order" integer DEFAULT 0 NOT NULL,
    "details" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamptz DEFAULT now() NOT NULL,
    "updated_at" timestamptz DEFAULT now() NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS ${qualified("max_business_actions")} (
    "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
    "max_user_id" text NOT NULL,
    "action_type" text NOT NULL,
    "status" text DEFAULT 'new' NOT NULL,
    "payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamptz DEFAULT now() NOT NULL,
    "updated_at" timestamptz DEFAULT now() NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS ${qualified("max_consents")} (
    "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
    "max_user_id" text NOT NULL,
    "consent_type" text NOT NULL,
    "granted" boolean NOT NULL,
    "policy_version" text NOT NULL,
    "created_at" timestamptz DEFAULT now() NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS ${qualified("max_analytics_events")} (
    "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
    "max_user_id" text NOT NULL,
    "event_name" text NOT NULL,
    "properties" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamptz DEFAULT now() NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS ${qualified("max_bot_outbox")} (
    "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
    "max_user_id" text NOT NULL,
    "message_type" text NOT NULL,
    "payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "status" text DEFAULT 'pending' NOT NULL,
    "attempts" integer DEFAULT 0 NOT NULL,
    "scheduled_at" timestamptz DEFAULT now() NOT NULL,
    "sent_at" timestamptz,
    "created_at" timestamptz DEFAULT now() NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS ${qualified("max_audit_log")} (
    "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
    "max_user_id" text NOT NULL,
    "action" text NOT NULL,
    "details" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamptz DEFAULT now() NOT NULL
  )`,
  foreignKey("max_business_actions"),
  foreignKey("max_consents"),
  foreignKey("max_analytics_events"),
  foreignKey("max_bot_outbox"),
  foreignKey("max_audit_log"),
];

const client = new Client({ connectionString: process.env.DATABASE_URL });

try {
  await client.connect();
  await client.query("BEGIN");
  for (const statement of statements) {
    await client.query(statement);
  }
  await client.query("COMMIT");
  console.log(`[entrypoint] MAX schema ready: ${schema}`);
} catch (error) {
  await client.query("ROLLBACK").catch(() => undefined);
  throw error;
} finally {
  await client.end().catch(() => undefined);
}
