import { pgTable, serial, text, timestamp } from "drizzle-orm/pg-core";

// The status CHECK lives in migrations/0001_create_clients.sql, as generated
// MAX apps declare it.
export const clients = pgTable("clients", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  phone: text("phone"),
  status: text("status").notNull().default("new"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});
