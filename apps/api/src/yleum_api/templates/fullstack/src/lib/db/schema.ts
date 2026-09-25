/**
 * Drizzle schema starter — single source of truth for the project's database.
 *
 * Conventions for the AI (also enshrined in the system prompt):
 * - Every table has `id` (uuid, pk), `created_at`, `updated_at`.
 * - Foreign keys CASCADE on delete.
 * - Money columns: `numeric(12, 4)`.
 * - Timestamps: `timestamptz`.
 *
 * After every AI write, the orchestrator runs `drizzle-kit push` so new tables
 * land in Postgres without the user having to ask.
 */

import { sql } from "drizzle-orm";
import { pgTable, text, timestamp, uuid } from "drizzle-orm/pg-core";

export const examples = pgTable("examples", {
  id: uuid("id").primaryKey().defaultRandom(),
  title: text("title").notNull(),
  body: text("body"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .default(sql`now()`),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .default(sql`now()`),
});

export type Example = typeof examples.$inferSelect;
export type NewExample = typeof examples.$inferInsert;
