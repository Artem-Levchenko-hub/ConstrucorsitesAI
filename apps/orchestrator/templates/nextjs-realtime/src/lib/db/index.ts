/**
 * Drizzle client over a shared pg Pool. FIXED template file.
 *
 * The pool is pinned to `globalThis` so Turbopack HMR doesn't leak a new pool
 * (and its connections) on every edit during development.
 */

import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

import * as schema from "./schema";

const g = globalThis as unknown as { __omniaPool?: Pool };

const pool =
  g.__omniaPool ??
  (g.__omniaPool = new Pool({
    connectionString: process.env.DATABASE_URL,
    max: 10,
  }));

export const db = drizzle(pool, { schema });
