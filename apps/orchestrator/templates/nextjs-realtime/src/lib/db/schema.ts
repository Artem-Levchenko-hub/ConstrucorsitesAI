/**
 * Database schema (Drizzle / Postgres) — FIXED template file.
 *
 * Four tables back the realtime stack:
 *   users           — credentials auth (email + bcrypt hash) + a coarse role.
 *   channels        — a conversation / room / feed the app talks over.
 *   channel_members — RELATION-BASED membership: who may read+write a channel.
 *                     This is the table `realtime/policy.ts` checks on every
 *                     subscribe and publish, so a non-member cannot see a DM or
 *                     class chat. THIS is what makes a messenger secure by
 *                     default (vs owner-scoping, which cannot express "members
 *                     of conversation X").
 *   messages        — persisted channel history (so a reload re-renders the
 *                     thread); realtime fan-out is layered on top by the hub.
 *
 * The AI building an app ADDS rows / channels and may add its own tables in a
 * separate file, but never edits these four — the engine and policy depend on
 * their exact shape.
 */

import { sql } from "drizzle-orm";
import {
  index,
  pgTable,
  text,
  timestamp,
  unique,
  uuid,
} from "drizzle-orm/pg-core";

export const users = pgTable("users", {
  id: uuid("id")
    .default(sql`gen_random_uuid()`)
    .primaryKey(),
  email: text("email").notNull().unique(),
  name: text("name"),
  image: text("image"),
  passwordHash: text("password_hash"),
  // Coarse role; the first real signup becomes "admin" (see auth.roleForNewUser).
  role: text("role").notNull().default("user"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .defaultNow()
    .notNull(),
});

export const channels = pgTable("channels", {
  // App-defined id used inside the channel string `conversation:<id>`. Text so
  // an app can use a slug ("lobby") or a uuid — both work.
  id: text("id").primaryKey(),
  kind: text("kind").notNull().default("conversation"),
  title: text("title"),
  createdBy: uuid("created_by"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .defaultNow()
    .notNull(),
});

export const channelMembers = pgTable(
  "channel_members",
  {
    id: uuid("id")
      .default(sql`gen_random_uuid()`)
      .primaryKey(),
    channelId: text("channel_id").notNull(),
    userId: uuid("user_id").notNull(),
    role: text("role").notNull().default("member"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (t) => ({
    uniqMember: unique("channel_members_channel_user_uniq").on(
      t.channelId,
      t.userId,
    ),
    byChannel: index("channel_members_channel_idx").on(t.channelId),
    byUser: index("channel_members_user_idx").on(t.userId),
  }),
);

export const messages = pgTable(
  "messages",
  {
    id: uuid("id")
      .default(sql`gen_random_uuid()`)
      .primaryKey(),
    channelId: text("channel_id").notNull(),
    userId: uuid("user_id").notNull(),
    type: text("type").notNull().default("message"),
    body: text("body").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (t) => ({
    byChannel: index("messages_channel_created_idx").on(
      t.channelId,
      t.createdAt,
    ),
  }),
);

export type User = typeof users.$inferSelect;
export type Channel = typeof channels.$inferSelect;
export type ChannelMember = typeof channelMembers.$inferSelect;
export type Message = typeof messages.$inferSelect;
