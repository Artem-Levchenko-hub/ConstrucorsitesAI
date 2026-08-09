import { sql } from "drizzle-orm";
import {
  boolean,
  integer,
  jsonb,
  pgSchema,
  text,
  timestamp,
  uuid,
} from "drizzle-orm/pg-core";

// Every generated app has a dedicated Postgres schema. Qualifying tables here
// keeps drizzle-kit foreign keys inside that tenant instead of silently
// targeting public.max_users when the connection uses a custom search_path.
const appSchema = pgSchema(process.env.OMNIA_DB_SCHEMA || "public");

export const maxUsers = appSchema.table("max_users", {
  id: uuid("id").primaryKey().defaultRandom(),
  maxUserId: text("max_user_id").notNull().unique(),
  firstName: text("first_name").notNull(),
  lastName: text("last_name"),
  username: text("username"),
  languageCode: text("language_code"),
  photoUrl: text("photo_url"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const maxWebhookEvents = appSchema.table("max_webhook_events", {
  id: uuid("id").primaryKey().defaultRandom(),
  eventKey: text("event_key").notNull().unique(),
  eventType: text("event_type").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const maxCatalogItems = appSchema.table("max_catalog_items", {
  id: uuid("id").primaryKey().defaultRandom(),
  externalId: text("external_id").notNull().unique(),
  title: text("title").notNull(),
  description: text("description").notNull().default(""),
  price: text("price").notNull().default(""),
  actionLabel: text("action_label").notNull().default("Открыть"),
  active: boolean("active").notNull().default(true),
  sortOrder: integer("sort_order").notNull().default(0),
  details: jsonb("details").$type<Record<string, unknown>>().notNull().default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const maxBusinessActions = appSchema.table("max_business_actions", {
  id: uuid("id").primaryKey().defaultRandom(),
  maxUserId: text("max_user_id")
    .notNull()
    .references(() => maxUsers.maxUserId, { onDelete: "cascade" }),
  actionType: text("action_type").notNull(),
  status: text("status").notNull().default("new"),
  payload: jsonb("payload").$type<Record<string, unknown>>().notNull().default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const maxConsents = appSchema.table("max_consents", {
  id: uuid("id").primaryKey().defaultRandom(),
  maxUserId: text("max_user_id")
    .notNull()
    .references(() => maxUsers.maxUserId, { onDelete: "cascade" }),
  consentType: text("consent_type").notNull(),
  granted: boolean("granted").notNull(),
  policyVersion: text("policy_version").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const maxAnalyticsEvents = appSchema.table("max_analytics_events", {
  id: uuid("id").primaryKey().defaultRandom(),
  maxUserId: text("max_user_id")
    .notNull()
    .references(() => maxUsers.maxUserId, { onDelete: "cascade" }),
  eventName: text("event_name").notNull(),
  properties: jsonb("properties").$type<Record<string, unknown>>().notNull().default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const maxBotOutbox = appSchema.table("max_bot_outbox", {
  id: uuid("id").primaryKey().defaultRandom(),
  maxUserId: text("max_user_id")
    .notNull()
    .references(() => maxUsers.maxUserId, { onDelete: "cascade" }),
  messageType: text("message_type").notNull(),
  payload: jsonb("payload").$type<Record<string, unknown>>().notNull().default({}),
  status: text("status").notNull().default("pending"),
  attempts: integer("attempts").notNull().default(0),
  scheduledAt: timestamp("scheduled_at", { withTimezone: true }).notNull().default(sql`now()`),
  sentAt: timestamp("sent_at", { withTimezone: true }),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const maxAuditLog = appSchema.table("max_audit_log", {
  id: uuid("id").primaryKey().defaultRandom(),
  maxUserId: text("max_user_id")
    .notNull()
    .references(() => maxUsers.maxUserId, { onDelete: "cascade" }),
  action: text("action").notNull(),
  details: jsonb("details").$type<Record<string, unknown>>().notNull().default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
});
