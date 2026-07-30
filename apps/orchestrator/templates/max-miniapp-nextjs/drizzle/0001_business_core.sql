CREATE TABLE IF NOT EXISTS "max_catalog_items" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "external_id" text NOT NULL UNIQUE,
  "title" text NOT NULL,
  "description" text NOT NULL DEFAULT '',
  "price" text NOT NULL DEFAULT '',
  "action_label" text NOT NULL DEFAULT 'Открыть',
  "active" boolean NOT NULL DEFAULT true,
  "sort_order" integer NOT NULL DEFAULT 0,
  "details" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "max_business_actions" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "max_user_id" text NOT NULL REFERENCES "max_users"("max_user_id") ON DELETE CASCADE,
  "action_type" text NOT NULL,
  "status" text NOT NULL DEFAULT 'new',
  "payload" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "max_business_actions_user_idx"
  ON "max_business_actions" ("max_user_id", "created_at");
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "max_consents" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "max_user_id" text NOT NULL REFERENCES "max_users"("max_user_id") ON DELETE CASCADE,
  "consent_type" text NOT NULL,
  "granted" boolean NOT NULL,
  "policy_version" text NOT NULL,
  "created_at" timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "max_consents_user_idx"
  ON "max_consents" ("max_user_id", "consent_type", "created_at");
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "max_analytics_events" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "max_user_id" text NOT NULL REFERENCES "max_users"("max_user_id") ON DELETE CASCADE,
  "event_name" text NOT NULL,
  "properties" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "max_analytics_events_user_idx"
  ON "max_analytics_events" ("max_user_id", "created_at");
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "max_bot_outbox" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "max_user_id" text NOT NULL REFERENCES "max_users"("max_user_id") ON DELETE CASCADE,
  "message_type" text NOT NULL,
  "payload" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "status" text NOT NULL DEFAULT 'pending',
  "attempts" integer NOT NULL DEFAULT 0,
  "scheduled_at" timestamptz NOT NULL DEFAULT now(),
  "sent_at" timestamptz,
  "created_at" timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "max_bot_outbox_pending_idx"
  ON "max_bot_outbox" ("status", "scheduled_at");
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "max_audit_log" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "max_user_id" text NOT NULL REFERENCES "max_users"("max_user_id") ON DELETE CASCADE,
  "action" text NOT NULL,
  "details" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "max_audit_log_user_idx"
  ON "max_audit_log" ("max_user_id", "created_at");
