import fs from "node:fs";
import path from "node:path";
import pg from "pg";

const { Pool } = pg;
const url = process.env.DATABASE_URL;
if (!url) throw new Error("DATABASE_URL is required");

const pool = new Pool({ connectionString: url, max: 1, connectionTimeoutMillis: 15000 });
const TRANSACTION_WORDS = new Set([
  "ABORT", "BEGIN", "COMMIT", "END", "RELEASE", "ROLLBACK", "SAVEPOINT",
]);
const isIdentifierContinuation = (char) => (
  typeof char === "string"
  && (char.codePointAt(0) >= 0x80 || /[A-Za-z0-9_$]/.test(char))
);

function sqlTokens(sql) {
  const tokens = [];
  let word = "";
  let i = 0;
  let blockDepth = 0;
  let quote = null;
  let backslashQuote = false;
  let dollar = null;
  const flush = () => {
    if (word) tokens.push(word.toUpperCase());
    word = "";
  };
  while (i < sql.length) {
    if (blockDepth) {
      if (sql.startsWith("/*", i)) { blockDepth += 1; i += 2; continue; }
      if (sql.startsWith("*/", i)) { blockDepth -= 1; i += 2; continue; }
      i += 1;
      continue;
    }
    if (dollar) {
      const end = sql.indexOf(dollar, i);
      if (end < 0) return tokens;
      i = end + dollar.length;
      dollar = null;
      continue;
    }
    if (quote) {
      if (backslashQuote && sql[i] === "\\") { i = Math.min(i + 2, sql.length); continue; }
      if (sql[i] === quote) {
        if (sql[i + 1] === quote) { i += 2; continue; }
        quote = null;
        backslashQuote = false;
      }
      i += 1;
      continue;
    }
    if (sql.startsWith("--", i)) {
      flush();
      const end = sql.indexOf("\n", i + 2);
      i = end < 0 ? sql.length : end + 1;
      continue;
    }
    if (sql.startsWith("/*", i)) { flush(); blockDepth = 1; i += 2; continue; }
    if (sql[i] === "'" || sql[i] === '"') {
      backslashQuote = sql[i] === "'" && word.toUpperCase() === "E";
      flush(); quote = sql[i]; i += 1; continue;
    }
    if (sql[i] === "$") {
      if (!isIdentifierContinuation(sql[i - 1])) {
        const match = sql.slice(i).match(/^\$[A-Za-z_][A-Za-z0-9_]*\$|^\$\$/);
        if (match) { flush(); dollar = match[0]; i += dollar.length; continue; }
      }
    }
    if (/[A-Za-z_]/.test(sql[i])) word += sql[i];
    else {
      flush();
      if (sql[i] === ";") tokens.push(";");
    }
    i += 1;
  }
  flush();
  return tokens;
}

function transactionControl(sql) {
  const tokens = sqlTokens(sql);
  const statements = [[]];
  for (const token of tokens) {
    if (token === ";") statements.push([]);
    else statements.at(-1).push(token);
  }
  for (const statement of statements) {
    if (!statement.length) continue;
    if (TRANSACTION_WORDS.has(statement[0])) return statement[0];
    if (statement[0] === "START" && statement[1] === "TRANSACTION") return "START TRANSACTION";
    if (statement[0] === "PREPARE" && statement[1] === "TRANSACTION") return "PREPARE TRANSACTION";
    if (statement[0] === "SET" && statement[1] === "TRANSACTION") return "SET TRANSACTION";
  }
  return null;
}

try {
  const client = await pool.connect();
  try {
    await client.query(
      "SELECT pg_advisory_lock(hashtext('omnia:max:migrations'), hashtext(current_schema()))",
    );
    const dir = path.join(process.cwd(), "drizzle");
    const files = fs.existsSync(dir)
      ? fs.readdirSync(dir).filter((name) => name.endsWith(".sql")).sort()
      : [];
    await client.query(`
      CREATE TABLE IF NOT EXISTS __omnia_migrations (
        name text PRIMARY KEY,
        applied_at timestamptz NOT NULL DEFAULT now()
      )
    `);
    for (const name of files) {
      const done = await client.query("SELECT 1 FROM __omnia_migrations WHERE name=$1", [name]);
      if (done.rowCount) continue;
      const sql = fs
        .readFileSync(path.join(dir, name), "utf8")
        .replaceAll("--> statement-breakpoint", "")
        // Drizzle emits FK targets as "public"."table". Hosted apps run in a
        // per-project search_path, so that qualifier escapes the isolated schema
        // and makes a valid migration fail with relation public.<table> missing.
        .replaceAll('"public".', "");
      const forbidden = transactionControl(sql);
      if (forbidden) {
        throw new Error(`migration ${name} contains forbidden transaction control: ${forbidden}`);
      }
      try {
        await client.query("BEGIN");
        await client.query(sql);
        await client.query("INSERT INTO __omnia_migrations(name) VALUES($1)", [name]);
        await client.query("COMMIT");
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      }
    }
    console.log(`[migrations] applied ${files.length} migration file(s)`);
  } finally {
    try {
      await client.query(
        "SELECT pg_advisory_unlock(hashtext('omnia:max:migrations'), hashtext(current_schema()))",
      );
    } finally {
      client.release();
    }
  }
} finally {
  await pool.end();
}
