import { check, integer, numeric, pgTable, serial, text, timestamp } from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";

export const clients = pgTable(
  "clients",
  {
    id: serial("id").primaryKey(),
    name: text("name").notNull(),
    phone: text("phone"),
    status: text("status").notNull().default("new"),
    createdAt: timestamp("created_at").notNull().defaultNow(),
    email: text("email").notNull(),
  },
  (t) => [check("clients_status_check", sql`${t.status} in ('new','vip')`)],
);

export const visits = pgTable("visits", {
  id: serial("id").primaryKey(),
  clientId: integer("client_id").notNull().references(() => clients.id, { onDelete: "cascade" }),
  amount: numeric("amount", { precision: 12, scale: 2 }).notNull(),
  visitedAt: timestamp("visited_at").notNull().defaultNow(),
});
