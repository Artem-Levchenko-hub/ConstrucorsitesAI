CREATE TABLE IF NOT EXISTS "max_users" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "max_user_id" text NOT NULL UNIQUE,
  "first_name" text NOT NULL,
  "last_name" text,
  "username" text,
  "language_code" text,
  "photo_url" text,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "max_webhook_events" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "event_key" text NOT NULL UNIQUE,
  "event_type" text NOT NULL,
  "created_at" timestamptz NOT NULL DEFAULT now()
);
