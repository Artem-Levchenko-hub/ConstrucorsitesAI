ALTER TABLE "max_business_actions"
  ADD COLUMN IF NOT EXISTS "idempotency_key" text;
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "max_business_actions_user_idempotency_key_uq"
  ON "max_business_actions" ("max_user_id", "idempotency_key");
