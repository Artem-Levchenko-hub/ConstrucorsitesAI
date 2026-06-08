/**
 * Drizzle schema — FIXED. This template's whole database is two things: the
 * Auth.js tables (below) and the generic `records` store (bottom). The AI does
 * NOT edit this file — business objects are entities/<Name>.json schemas served
 * by the engine over the single `records` table, so there are no per-entity
 * tables and no migrations. `docker-entrypoint.sh` runs `db:push --force` on
 * container start to materialise exactly these tables.
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
  index,
  integer,
  jsonb,
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

// ─── Generic record store — the whole data backend (DO NOT EDIT) ──────────
//
// Base44-style entity engine. Every business object the app defines (Task,
// Invoice, Post, …) is a row here, discriminated by `entity`, with its fields
// living in the `data` JSONB column. There are NO per-entity tables and NO
// migrations when the app adds an entity — the AI just drops an
// `entities/<Name>.json` schema and the engine (src/app/api/entities/**) reads
// + validates against it. This single fixed table is the only domain DDL.
//
// `created_by` scopes ownership: the engine filters every read/write by it for
// `owner`-access entities, so one user can never read another's rows. Keep this
// table and its shape exactly as-is — the engine depends on these column names.

export const records = pgTable(
  "records",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    /** Entity name, e.g. "Task" — matches an entities/<Name>.json schema. */
    entity: text("entity").notNull(),
    /** The entity's fields, validated against its JSON schema on write. */
    data: jsonb("data").notNull().$type<Record<string, unknown>>(),
    /** Owner. FK to users — `owner`-access reads/writes are scoped to this. */
    createdBy: uuid("created_by")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .default(sql`now()`),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .default(sql`now()`),
  },
  (t) => ({
    // Hot path: "list my <Entity> rows, newest first" — covered by this btree
    // (Postgres scans it backward for created_at DESC).
    byEntityOwner: index("records_entity_owner_created_idx").on(
      t.entity,
      t.createdBy,
      t.createdAt,
    ),
  }),
);

export type RecordRow = typeof records.$inferSelect;
export type NewRecordRow = typeof records.$inferInsert;
