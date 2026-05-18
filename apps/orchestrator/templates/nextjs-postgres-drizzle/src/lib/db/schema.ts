/**
 * Drizzle schema — single source of truth for the database.
 *
 * Conventions enforced by the AI:
 * - Every table has `id` (uuid, pk), `created_at`, `updated_at`.
 * - Foreign keys CASCADE on delete.
 * - Money columns: `numeric(12, 4)` — 4 decimals matches Omnia core wallet.
 * - Timestamps: `timestamptz` (timezone-aware).
 *
 * The orchestrator runs `drizzle-kit generate` after every AI write, so a new
 * migration appears in `./drizzle/` automatically. `drizzle-kit migrate` is
 * triggered on container start.
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
