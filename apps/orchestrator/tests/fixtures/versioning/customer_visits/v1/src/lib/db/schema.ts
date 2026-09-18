import { check, pgTable, serial, text, timestamp } from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";

export const clients = pgTable(
  "clients",
  {
    id: serial("id").primaryKey(),
    name: text("name").notNull(),
    phone: text("phone"),
    status: text("status").notNull().default("new"),
    createdAt: timestamp("created_at").notNull().defaultNow(),
  },
  (t) => [check("clients_status_check", sql`${t.status} in ('new','vip')`)],
);
