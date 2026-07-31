import fs from "node:fs";
import path from "node:path";
import pg from "pg";

const { Pool } = pg;
const url = process.env.DATABASE_URL;
if (!url) throw new Error("DATABASE_URL is required");

const pool = new Pool({ connectionString: url, max: 1, connectionTimeoutMillis: 15000 });
try {
  const dir = path.join(process.cwd(), "drizzle");
  const files = fs.existsSync(dir)
    ? fs.readdirSync(dir).filter((name) => name.endsWith(".sql")).sort()
    : [];
  await pool.query(`
    CREATE TABLE IF NOT EXISTS __omnia_migrations (
      name text PRIMARY KEY,
      applied_at timestamptz NOT NULL DEFAULT now()
    )
  `);
  for (const name of files) {
    const done = await pool.query("SELECT 1 FROM __omnia_migrations WHERE name=$1", [name]);
    if (done.rowCount) continue;
    const sql = fs
      .readFileSync(path.join(dir, name), "utf8")
      .replaceAll("--> statement-breakpoint", "")
      // Drizzle emits FK targets as "public"."table". Hosted apps run in a
      // per-project search_path, so that qualifier escapes the isolated schema
      // and makes a valid migration fail with relation public.<table> missing.
      .replaceAll('"public".', "");
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await client.query(sql);
      await client.query("INSERT INTO __omnia_migrations(name) VALUES($1)", [name]);
      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }
  console.log(`[migrations] applied ${files.length} migration file(s)`);
} finally {
  await pool.end();
}
