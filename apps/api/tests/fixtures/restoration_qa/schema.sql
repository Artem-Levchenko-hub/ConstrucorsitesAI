-- Схема одноразового свидетеля. Только собственные таблицы фикстуры:
-- всё остальное разрушило бы её главное свойство — полностью известное состояние.
CREATE TABLE IF NOT EXISTS "qa_clients" (
  "id" uuid PRIMARY KEY NOT NULL,
  "owner_id" uuid NOT NULL,
  "client_name" text NOT NULL,
  "note" text,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS "qa_client_history" (
  "id" uuid PRIMARY KEY NOT NULL,
  "client_id" uuid NOT NULL REFERENCES "qa_clients"("id") ON DELETE CASCADE,
  "owner_id" uuid NOT NULL,
  "action" text NOT NULL,
  "details" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz NOT NULL
);
