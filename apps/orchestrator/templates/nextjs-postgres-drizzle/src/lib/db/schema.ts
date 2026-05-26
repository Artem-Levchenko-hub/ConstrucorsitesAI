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
 *
 * ───────────────────────────────────────────────────────────────────────────
 * AUTH TABLES — pre-wired Auth.js v5 Drizzle adapter schema.
 *
 * These four tables (`users`, `accounts`, `sessions`, `verificationTokens`)
 * are required by `@auth/drizzle-adapter` and MUST exist before any auth
 * flow works. AI MUST NOT delete or rename them — the auth.ts config
 * references them by exact name. New columns are fine to add (e.g.
 * `phoneNumber`, `subscription`) but `email` / `passwordHash` / `role`
 * have semantics that other auth code depends on.
 *
 * The `users.role` column is a free-form text but conventionally one of
 * `user` (default for new signups) or `admin` (manually promoted). The
 * `<Protected role="admin">` helper checks against this value.
 * ───────────────────────────────────────────────────────────────────────────
 */

import type { AdapterAccountType } from "next-auth/adapters";
import { sql } from "drizzle-orm";
import {
  integer,
  pgTable,
  primaryKey,
  text,
  timestamp,
  uuid,
} from "drizzle-orm/pg-core";

// ─── Auth tables (DO NOT RENAME, see top-of-file note) ────────────────────

export const users = pgTable("users", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: text("name"),
  email: text("email").notNull().unique(),
  emailVerified: timestamp("email_verified", { withTimezone: true }),
  image: text("image"),
  /** bcrypt hash. Set by signup flow, never sent to the client.
   *  Null is allowed so OAuth-only users (Google/GitHub) can exist
   *  without a password — they sign in through providers. */
  passwordHash: text("password_hash"),
  /** Conventionally "user" or "admin". `<Protected role="admin">` checks this. */
  role: text("role").notNull().default("user"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .default(sql`now()`),
});

export const accounts = pgTable(
  "accounts",
  {
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    type: text("type").$type<AdapterAccountType>().notNull(),
    provider: text("provider").notNull(),
    providerAccountId: text("provider_account_id").notNull(),
    refresh_token: text("refresh_token"),
    access_token: text("access_token"),
    expires_at: integer("expires_at"),
    token_type: text("token_type"),
    scope: text("scope"),
    id_token: text("id_token"),
    session_state: text("session_state"),
  },
  (account) => ({
    pk: primaryKey({ columns: [account.provider, account.providerAccountId] }),
  }),
);

export const sessions = pgTable("sessions", {
  sessionToken: text("session_token").primaryKey(),
  userId: uuid("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  expires: timestamp("expires", { withTimezone: true }).notNull(),
});

export const verificationTokens = pgTable(
  "verification_tokens",
  {
    identifier: text("identifier").notNull(),
    token: text("token").notNull(),
    expires: timestamp("expires", { withTimezone: true }).notNull(),
  },
  (vt) => ({
    pk: primaryKey({ columns: [vt.identifier, vt.token] }),
  }),
);

export type User = typeof users.$inferSelect;
export type NewUser = typeof users.$inferInsert;

// ─── Example domain table (AI replaces this with real schema per prompt) ───

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
