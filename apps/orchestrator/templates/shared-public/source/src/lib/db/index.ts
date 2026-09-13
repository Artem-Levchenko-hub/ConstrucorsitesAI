/**
 * Single DB client. Pooled via node-postgres; orchestrator provisions the
 * connection string per project (one Postgres schema per project — see
 * docs/08-vps-setup.md).
 */
import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

import * as schema from "./schema";

if (!process.env.DATABASE_URL) {
  throw new Error("DATABASE_URL is required (orchestrator should inject it)");
}

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 10,
  idleTimeoutMillis: 30_000,
});

export const db = drizzle(pool, { schema });
export { schema };
