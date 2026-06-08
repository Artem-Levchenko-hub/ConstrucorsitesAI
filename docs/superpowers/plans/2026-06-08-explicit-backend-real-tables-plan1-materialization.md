# Explicit Backend — Real Tables: Materialization Core (Plan 1 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile `entities/*.json` into real per-entity Postgres tables (typed columns, indexes, FKs) that evolve additively-safely — without yet changing how the running app reads data.

**Architecture:** Pure planning layer (field→column typemap → per-entity TablePlan → DDL strings) + a reconciler that introspects the live schema, applies only additive DDL (CREATE TABLE / ADD COLUMN / CREATE INDEX / ADD FK), and reports destructive diffs instead of applying them. A codegen step emits a human-readable `entity-schema.ts` (Drizzle) as the "explicit" artifact. All gated behind `ENTITY_REAL_TABLES`; the engine still reads `records` (Plan 2 flips it).

**Tech Stack:** TypeScript, `pg` (already a dep), Drizzle (codegen output only), vitest + `@electric-sql/pglite` (in-process Postgres for tests), `tsx` (boot-time runner).

**Scope note:** Only the `nextjs-entities` template (`apps/orchestrator/templates/nextjs-entities/`). The engine rewrite, orchestrator `hot_reload` wiring, and end-to-end flip live in Plan 2. Prod (`next build`) reconcile path is also Plan 2; Plan 1 targets the dev-container boot path.

**Identifier safety:** entity + field names are already validated by `NAME_RE = /^[A-Za-z][A-Za-z0-9_]*$/` in `registry.ts`. All table/column names in this plan derive from those validated names via `snakeCase`, so generated SQL interpolates only `[a-z0-9_]` identifiers (values are still parameterized in Plan 2's engine).

---

## File Structure

All paths under `apps/orchestrator/templates/nextjs-entities/`.

- Create `src/lib/db/typemap.ts` — `snakeCase()` + `columnSpec(fieldName, FieldDef) → ColumnSpec`. One responsibility: map one entity field to one SQL column.
- Create `src/lib/db/tableplan.ts` — `tablePlan(EntityDef) → TablePlan` (table name, entity columns, indexes) + `SERVICE_COLUMNS`. One responsibility: full table shape for one entity.
- Create `src/lib/db/ddl.ts` — pure SQL string builders (`createTable`, `addColumn`, `addForeignKey`, `createIndex`). One responsibility: TablePlan/ColumnSpec → DDL text.
- Create `src/lib/db/introspect.ts` — `QueryRunner` interface + `introspect(q) → CurrentSchema`. One responsibility: read live schema into a comparable map.
- Create `src/lib/db/diff.ts` — `diffSchema(TablePlan[], CurrentSchema) → DiffResult`. One responsibility: decide additive ops vs destructive report.
- Create `src/lib/db/reconcile.ts` — `reconcile(q, EntityDef[]) → ReconcileReport`. One responsibility: orchestrate load→diff→apply additive in a transaction.
- Create `src/lib/db/codegen-schema.ts` — `generateEntitySchemaSource(EntityDef[]) → string`. One responsibility: emit the Drizzle `entity-schema.ts` artifact.
- Create `scripts/reconcile.ts` — boot/CLI entry: load entities, open `pg` pool, run `reconcile`, write `entity-schema.ts`, log report. Run via `tsx`.
- Modify `package.json` — add devDeps (`vitest`, `@electric-sql/pglite`, `tsx`) + scripts (`test`, `db:reconcile`).
- Create `vitest.config.ts` — test config.
- Modify `docker-entrypoint.sh` — after `init-db.mjs`, if `ENTITY_REAL_TABLES=true`, run `tsx scripts/reconcile.ts` (fail-soft).
- Tests under `src/lib/db/__tests__/`.

---

## Task 1: Test + boot tooling

**Files:**
- Modify: `apps/orchestrator/templates/nextjs-entities/package.json`
- Create: `apps/orchestrator/templates/nextjs-entities/vitest.config.ts`
- Create: `apps/orchestrator/templates/nextjs-entities/src/lib/db/__tests__/smoke.test.ts`

- [ ] **Step 1: Add devDeps + scripts to package.json**

Add to `devDependencies` (keep existing entries): `"vitest": "^2.1.9"`, `"@electric-sql/pglite": "^0.2.17"`, `"tsx": "^4.19.2"`. Add to `scripts`: `"test": "vitest run"`, `"db:reconcile": "tsx scripts/reconcile.ts"`.

- [ ] **Step 2: Create vitest.config.ts**

```ts
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: { environment: "node", include: ["src/**/*.test.ts"] },
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
});
```

- [ ] **Step 3: Write a smoke test (pglite boots)**

```ts
import { describe, it, expect } from "vitest";
import { PGlite } from "@electric-sql/pglite";

describe("pglite", () => {
  it("runs DDL + reads information_schema", async () => {
    const db = new PGlite();
    await db.exec(`CREATE TABLE t (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text);`);
    const r = await db.query<{ column_name: string }>(
      `SELECT column_name FROM information_schema.columns WHERE table_name = 't' ORDER BY column_name`,
    );
    expect(r.rows.map((x) => x.column_name)).toEqual(["id", "name"]);
  });
});
```

- [ ] **Step 4: Install + run**

Run: `cd apps/orchestrator/templates/nextjs-entities && pnpm install && pnpm test`
Expected: PASS (1 test). If pglite lacks `gen_random_uuid`, the test still passes (column read only).

- [ ] **Step 5: Commit**

```bash
git add apps/orchestrator/templates/nextjs-entities/package.json \
  apps/orchestrator/templates/nextjs-entities/pnpm-lock.yaml \
  apps/orchestrator/templates/nextjs-entities/vitest.config.ts \
  apps/orchestrator/templates/nextjs-entities/src/lib/db/__tests__/smoke.test.ts
git commit -m "test(nextjs-entities): add vitest + pglite + tsx tooling"
```

---

## Task 2: typemap — field → column

**Files:**
- Create: `src/lib/db/typemap.ts`
- Test: `src/lib/db/__tests__/typemap.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
import { describe, it, expect } from "vitest";
import { snakeCase, columnSpec } from "@/lib/db/typemap";

describe("snakeCase", () => {
  it("converts camel/Pascal to snake", () => {
    expect(snakeCase("Lipstick")).toBe("lipstick");
    expect(snakeCase("lipstickId")).toBe("lipstick_id");
    expect(snakeCase("inStock")).toBe("in_stock");
    expect(snakeCase("price")).toBe("price");
  });
});

describe("columnSpec", () => {
  it("maps scalar types", () => {
    expect(columnSpec("name", { type: "string" })).toMatchObject({ name: "name", sqlType: "text" });
    expect(columnSpec("bio", { type: "text" })).toMatchObject({ sqlType: "text" });
    expect(columnSpec("price", { type: "number" })).toMatchObject({ sqlType: "numeric" });
    expect(columnSpec("inStock", { type: "boolean" })).toMatchObject({ name: "in_stock", sqlType: "boolean" });
    expect(columnSpec("dueAt", { type: "date" })).toMatchObject({ name: "due_at", sqlType: "timestamptz" });
  });
  it("enum → text + CHECK over options (quotes escaped)", () => {
    const c = columnSpec("size", { type: "enum", options: ["S", "M", "O'L"] });
    expect(c.sqlType).toBe("text");
    expect(c.check).toBe(`"size" IN ('S', 'M', 'O''L')`);
  });
  it("enum with no options → plain text, no check", () => {
    expect(columnSpec("k", { type: "enum" }).check).toBeUndefined();
  });
  it("reference → uuid + target table", () => {
    const c = columnSpec("lipstickId", { type: "reference", entity: "Lipstick" });
    expect(c).toMatchObject({ name: "lipstick_id", sqlType: "uuid", referencesTable: "lipstick" });
  });
  it("entity columns are always nullable (additive-safe)", () => {
    expect(columnSpec("name", { type: "string", required: true }).notNull).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test src/lib/db/__tests__/typemap.test.ts`
Expected: FAIL — cannot find module `@/lib/db/typemap`.

- [ ] **Step 3: Implement typemap.ts**

```ts
/**
 * Maps one entity field (registry.FieldDef) to one Postgres column spec.
 * Entity columns are ALWAYS nullable so `ADD COLUMN` to a table with existing
 * rows can never fail — "required" is enforced at the app layer (registry's
 * createSchema/zod), not by a NOT NULL the AI could trip over mid-evolution.
 * Pure module; the AI never edits it.
 */
import type { FieldDef } from "@/lib/entities/registry";

export interface ColumnSpec {
  /** snake_case column name. */
  name: string;
  /** Base SQL type: text | numeric | boolean | timestamptz | uuid. */
  sqlType: string;
  /** Always false for entity fields (see module note). */
  notNull: boolean;
  /** For enum: a CHECK body like `"size" IN ('S','M')` (no constraint name). */
  check?: string;
  /** For reference: snake_case target table; column FKs to <table>.id. */
  referencesTable?: string;
}

/** camelCase / PascalCase → snake_case (identifiers are already [A-Za-z0-9_]). */
export function snakeCase(name: string): string {
  return name
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/__+/g, "_")
    .toLowerCase();
}

function quoteLiteral(v: string): string {
  return `'${v.replace(/'/g, "''")}'`;
}

export function columnSpec(fieldName: string, f: FieldDef): ColumnSpec {
  const name = snakeCase(fieldName);
  const base: ColumnSpec = { name, sqlType: "text", notNull: false };
  switch (f.type) {
    case "number":
      return { ...base, sqlType: "numeric" };
    case "boolean":
      return { ...base, sqlType: "boolean" };
    case "date":
      return { ...base, sqlType: "timestamptz" };
    case "enum":
      if (f.options && f.options.length) {
        const list = f.options.map(quoteLiteral).join(", ");
        return { ...base, check: `"${name}" IN (${list})` };
      }
      return base;
    case "reference":
      return {
        ...base,
        sqlType: "uuid",
        referencesTable: f.entity ? snakeCase(f.entity) : undefined,
      };
    case "string":
    case "text":
    default:
      return base;
  }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm test src/lib/db/__tests__/typemap.test.ts`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add src/lib/db/typemap.ts src/lib/db/__tests__/typemap.test.ts
git commit -m "feat(db): field→column typemap (entity cols nullable, enum CHECK, ref→uuid)"
```

---

## Task 3: tableplan — entity → table shape

**Files:**
- Create: `src/lib/db/tableplan.ts`
- Test: `src/lib/db/__tests__/tableplan.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
import { describe, it, expect } from "vitest";
import { tablePlan, SERVICE_COLUMNS } from "@/lib/db/tableplan";
import type { EntityDef } from "@/lib/entities/registry";

const lipstick: EntityDef = {
  name: "Lipstick",
  access: "public",
  fields: { name: { type: "string" }, price: { type: "number" } },
};
const cartItem: EntityDef = {
  name: "CartItem",
  access: "owner",
  fields: { lipstickId: { type: "reference", entity: "Lipstick" }, quantity: { type: "number" } },
};

describe("tablePlan", () => {
  it("derives snake table name + entity columns (no service cols in columns[])", () => {
    const p = tablePlan(lipstick);
    expect(p.table).toBe("lipstick");
    expect(p.columns.map((c) => c.name)).toEqual(["name", "price"]);
  });
  it("indexes created_by + each reference column", () => {
    const p = tablePlan(cartItem);
    const names = p.indexes.map((i) => i.name).sort();
    expect(names).toContain("cart_item_created_by_idx");
    expect(names).toContain("cart_item_lipstick_id_idx");
  });
  it("SERVICE_COLUMNS are the four reserved names", () => {
    expect([...SERVICE_COLUMNS].sort()).toEqual(["created_at", "created_by", "id", "updated_at"]);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test src/lib/db/__tests__/tableplan.test.ts`
Expected: FAIL — cannot find module `@/lib/db/tableplan`.

- [ ] **Step 3: Implement tableplan.ts**

```ts
/**
 * Full table shape for one entity: snake table name, the entity-field columns,
 * and the indexes we always want (owner lookups + every reference column).
 * Service columns (id/created_by/created_at/updated_at) are implicit — emitted
 * by ddl.createTable, never part of `columns`. Pure module.
 */
import type { EntityDef } from "@/lib/entities/registry";
import { ColumnSpec, columnSpec, snakeCase } from "@/lib/db/typemap";

export const SERVICE_COLUMNS = new Set(["id", "created_by", "created_at", "updated_at"]);

export interface IndexSpec {
  name: string;
  columns: string[];
}

export interface TablePlan {
  /** snake_case(entity.name) */
  table: string;
  /** Original entity name (for reports/codegen). */
  entity: string;
  /** Entity-field columns only (service columns are implicit). */
  columns: ColumnSpec[];
  indexes: IndexSpec[];
}

export function tablePlan(def: EntityDef): TablePlan {
  const table = snakeCase(def.name);
  const columns: ColumnSpec[] = Object.entries(def.fields).map(([k, f]) => columnSpec(k, f));

  const indexes: IndexSpec[] = [
    { name: `${table}_created_by_idx`, columns: ["created_by"] },
  ];
  for (const c of columns) {
    if (c.referencesTable) {
      indexes.push({ name: `${table}_${c.name}_idx`, columns: [c.name] });
    }
  }
  return { table, entity: def.name, columns, indexes };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm test src/lib/db/__tests__/tableplan.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/db/tableplan.ts src/lib/db/__tests__/tableplan.test.ts
git commit -m "feat(db): entity→TablePlan (table name, columns, owner+reference indexes)"
```

---

## Task 4: ddl — SQL string builders

**Files:**
- Create: `src/lib/db/ddl.ts`
- Test: `src/lib/db/__tests__/ddl.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
import { describe, it, expect } from "vitest";
import { createTable, addColumn, addForeignKey, createIndex } from "@/lib/db/ddl";
import { tablePlan } from "@/lib/db/tableplan";
import { columnSpec } from "@/lib/db/typemap";

const plan = tablePlan({
  name: "CartItem",
  access: "owner",
  fields: { lipstickId: { type: "reference", entity: "Lipstick" }, quantity: { type: "number" } },
});

describe("createTable", () => {
  const sql = createTable(plan);
  it("includes service columns + entity columns (nullable)", () => {
    expect(sql).toContain(`CREATE TABLE IF NOT EXISTS "cart_item"`);
    expect(sql).toContain(`"id" uuid PRIMARY KEY DEFAULT gen_random_uuid()`);
    expect(sql).toContain(`"created_by" uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE`);
    expect(sql).toContain(`"lipstick_id" uuid`);
    expect(sql).toContain(`"quantity" numeric`);
  });
  it("does NOT inline reference FKs (added in a second pass)", () => {
    expect(sql).not.toContain(`REFERENCES "lipstick"`);
  });
});

describe("addColumn", () => {
  it("is additive + idempotent", () => {
    expect(addColumn("cart_item", columnSpec("note", { type: "string" })))
      .toBe(`ALTER TABLE "cart_item" ADD COLUMN IF NOT EXISTS "note" text;`);
  });
});

describe("addForeignKey", () => {
  it("wraps in a DO block so re-run never errors on duplicate", () => {
    const sql = addForeignKey("cart_item", columnSpec("lipstickId", { type: "reference", entity: "Lipstick" }));
    expect(sql).toContain(`ALTER TABLE "cart_item" ADD CONSTRAINT "cart_item_lipstick_id_fkey"`);
    expect(sql).toContain(`FOREIGN KEY ("lipstick_id") REFERENCES "lipstick"(id) ON DELETE SET NULL`);
    expect(sql).toContain("duplicate_object");
  });
});

describe("createIndex", () => {
  it("is idempotent", () => {
    expect(createIndex("cart_item", { name: "cart_item_lipstick_id_idx", columns: ["lipstick_id"] }))
      .toBe(`CREATE INDEX IF NOT EXISTS "cart_item_lipstick_id_idx" ON "cart_item" ("lipstick_id");`);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test src/lib/db/__tests__/ddl.test.ts`
Expected: FAIL — cannot find module `@/lib/db/ddl`.

- [ ] **Step 3: Implement ddl.ts**

```ts
/**
 * Pure SQL builders. Reference FKs are added in a SECOND pass (addForeignKey)
 * after all tables exist, so creation order never matters. FK uses ON DELETE
 * SET NULL (the column is nullable) — deleting a referenced row blanks the link
 * instead of cascading deletes the user didn't ask for. Pure module.
 */
import type { ColumnSpec } from "@/lib/db/typemap";
import type { TablePlan } from "@/lib/db/tableplan";

export function createTable(plan: TablePlan): string {
  const lines: string[] = [
    `"id" uuid PRIMARY KEY DEFAULT gen_random_uuid()`,
  ];
  for (const c of plan.columns) {
    let line = `"${c.name}" ${c.sqlType}`;
    if (c.check) line += ` CHECK (${c.check})`;
    lines.push(line);
  }
  lines.push(`"created_by" uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE`);
  lines.push(`"created_at" timestamptz NOT NULL DEFAULT now()`);
  lines.push(`"updated_at" timestamptz NOT NULL DEFAULT now()`);
  return `CREATE TABLE IF NOT EXISTS "${plan.table}" (\n  ${lines.join(",\n  ")}\n);`;
}

export function addColumn(table: string, c: ColumnSpec): string {
  // Additive on an existing (possibly non-empty) table: nullable, no CHECK
  // (enum validity stays enforced at the app layer to avoid scanning old rows).
  return `ALTER TABLE "${table}" ADD COLUMN IF NOT EXISTS "${c.name}" ${c.sqlType};`;
}

export function addForeignKey(table: string, c: ColumnSpec): string {
  const name = `${table}_${c.name}_fkey`;
  return [
    `DO $$ BEGIN`,
    `  ALTER TABLE "${table}" ADD CONSTRAINT "${name}"`,
    `    FOREIGN KEY ("${c.name}") REFERENCES "${c.referencesTable}"(id) ON DELETE SET NULL;`,
    `EXCEPTION WHEN duplicate_object THEN NULL; END $$;`,
  ].join("\n");
}

export function createIndex(table: string, idx: { name: string; columns: string[] }): string {
  const cols = idx.columns.map((c) => `"${c}"`).join(", ");
  return `CREATE INDEX IF NOT EXISTS "${idx.name}" ON "${table}" (${cols});`;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm test src/lib/db/__tests__/ddl.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/db/ddl.ts src/lib/db/__tests__/ddl.test.ts
git commit -m "feat(db): idempotent DDL builders (create/add-column/add-fk/index)"
```

---

## Task 5: introspect — live schema → comparable map

**Files:**
- Create: `src/lib/db/introspect.ts`
- Test: `src/lib/db/__tests__/introspect.test.ts`

- [ ] **Step 1: Write failing test (pglite integration)**

```ts
import { describe, it, expect } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { introspect } from "@/lib/db/introspect";

describe("introspect", () => {
  it("returns table → (column → base type) for the current schema", async () => {
    const db = new PGlite();
    await db.exec(`CREATE TABLE lipstick (id uuid, name text, price numeric);`);
    const current = await introspect(db);
    expect(current.has("lipstick")).toBe(true);
    const cols = current.get("lipstick")!;
    expect(cols.get("name")).toBe("text");
    expect(cols.get("price")).toBe("numeric");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test src/lib/db/__tests__/introspect.test.ts`
Expected: FAIL — cannot find module `@/lib/db/introspect`.

- [ ] **Step 3: Implement introspect.ts**

```ts
/**
 * Reads the CURRENT schema (the one on search_path — the project's schema in
 * the dev container) into a comparable map: table → (column → base type).
 * `QueryRunner` is the minimal surface both `pg.Pool` and PGlite satisfy, so the
 * reconciler is testable in-process. Pure-ish (read-only).
 */
export interface QueryRunner {
  query<T = Record<string, unknown>>(
    sql: string,
    params?: unknown[],
  ): Promise<{ rows: T[] }>;
}

/** table name → (column name → udt/base type, e.g. "text", "numeric", "uuid"). */
export type CurrentSchema = Map<string, Map<string, string>>;

export async function introspect(q: QueryRunner): Promise<CurrentSchema> {
  const { rows } = await q.query<{ table_name: string; column_name: string; data_type: string; udt_name: string }>(
    `SELECT table_name, column_name, data_type, udt_name
       FROM information_schema.columns
      WHERE table_schema = current_schema()`,
  );
  const out: CurrentSchema = new Map();
  for (const r of rows) {
    let cols = out.get(r.table_name);
    if (!cols) {
      cols = new Map();
      out.set(r.table_name, cols);
    }
    cols.set(r.column_name, normalizeType(r.data_type, r.udt_name));
  }
  return out;
}

/** Collapse information_schema's verbose types to our typemap vocabulary. */
function normalizeType(dataType: string, udt: string): string {
  const d = dataType.toLowerCase();
  if (d === "timestamp with time zone") return "timestamptz";
  if (d === "character varying" || d === "text") return "text";
  if (d === "numeric" || d === "double precision" || d === "integer" || d === "bigint") return "numeric";
  if (d === "boolean") return "boolean";
  if (d === "uuid") return "uuid";
  return udt.toLowerCase();
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm test src/lib/db/__tests__/introspect.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/db/introspect.ts src/lib/db/__tests__/introspect.test.ts
git commit -m "feat(db): schema introspection (table→column→base-type)"
```

---

## Task 6: diff — additive ops vs destructive report

**Files:**
- Create: `src/lib/db/diff.ts`
- Test: `src/lib/db/__tests__/diff.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
import { describe, it, expect } from "vitest";
import { diffSchema } from "@/lib/db/diff";
import { tablePlan } from "@/lib/db/tableplan";
import type { CurrentSchema } from "@/lib/db/introspect";
import type { EntityDef } from "@/lib/entities/registry";

const lip: EntityDef = { name: "Lipstick", access: "public", fields: { name: { type: "string" }, price: { type: "number" } } };

describe("diffSchema", () => {
  it("missing table → CREATE TABLE + its indexes, no destructive", () => {
    const r = diffSchema([tablePlan(lip)], new Map());
    expect(r.additive.some((s) => s.includes(`CREATE TABLE IF NOT EXISTS "lipstick"`))).toBe(true);
    expect(r.additive.some((s) => s.includes(`CREATE INDEX IF NOT EXISTS "lipstick_created_by_idx"`))).toBe(true);
    expect(r.destructive).toEqual([]);
  });

  it("existing table missing a field → ADD COLUMN", () => {
    const current: CurrentSchema = new Map([
      ["lipstick", new Map([["id", "uuid"], ["name", "text"], ["created_by", "uuid"], ["created_at", "timestamptz"], ["updated_at", "timestamptz"]])],
    ]);
    const r = diffSchema([tablePlan(lip)], current);
    expect(r.additive.some((s) => s.includes(`ADD COLUMN IF NOT EXISTS "price" numeric`))).toBe(true);
    expect(r.destructive).toEqual([]);
  });

  it("column dropped from declaration → destructive report, NOT dropped", () => {
    const current: CurrentSchema = new Map([
      ["lipstick", new Map([["id", "uuid"], ["name", "text"], ["price", "numeric"], ["legacy", "text"], ["created_by", "uuid"], ["created_at", "timestamptz"], ["updated_at", "timestamptz"]])],
    ]);
    const r = diffSchema([tablePlan(lip)], current);
    expect(r.destructive).toContainEqual({ table: "lipstick", column: "legacy", reason: "removed-field" });
    expect(r.additive.join("\n")).not.toContain("legacy");
  });

  it("type change → destructive report, NOT altered", () => {
    const current: CurrentSchema = new Map([
      ["lipstick", new Map([["id", "uuid"], ["name", "text"], ["price", "text"], ["created_by", "uuid"], ["created_at", "timestamptz"], ["updated_at", "timestamptz"]])],
    ]);
    const r = diffSchema([tablePlan(lip)], current);
    expect(r.destructive).toContainEqual({ table: "lipstick", column: "price", reason: "type-change: text→numeric" });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test src/lib/db/__tests__/diff.test.ts`
Expected: FAIL — cannot find module `@/lib/db/diff`.

- [ ] **Step 3: Implement diff.ts**

```ts
/**
 * Compares desired TablePlans against the live schema and returns ONLY additive
 * DDL to run, plus a report of destructive diffs we deliberately DO NOT apply
 * (dropped fields, type changes). Additive ops are ordered: create all tables →
 * add missing columns → add reference FKs (all tables now exist) → indexes.
 * Pure module.
 */
import type { CurrentSchema } from "@/lib/db/introspect";
import type { TablePlan } from "@/lib/db/tableplan";
import { SERVICE_COLUMNS } from "@/lib/db/tableplan";
import { addColumn, addForeignKey, createIndex, createTable } from "@/lib/db/ddl";

export interface DestructiveItem {
  table: string;
  column: string;
  reason: string;
}
export interface DiffResult {
  additive: string[];
  destructive: DestructiveItem[];
}

export function diffSchema(plans: TablePlan[], current: CurrentSchema): DiffResult {
  const creates: string[] = [];
  const alters: string[] = [];
  const fks: string[] = [];
  const indexes: string[] = [];
  const destructive: DestructiveItem[] = [];

  for (const plan of plans) {
    const existing = current.get(plan.table);

    if (!existing) {
      creates.push(createTable(plan));
    } else {
      for (const c of plan.columns) {
        const have = existing.get(c.name);
        if (have === undefined) {
          alters.push(addColumn(plan.table, c));
        } else if (have !== c.sqlType) {
          destructive.push({ table: plan.table, column: c.name, reason: `type-change: ${have}→${c.sqlType}` });
        }
      }
      const planned = new Set(plan.columns.map((c) => c.name));
      for (const col of existing.keys()) {
        if (!planned.has(col) && !SERVICE_COLUMNS.has(col)) {
          destructive.push({ table: plan.table, column: col, reason: "removed-field" });
        }
      }
    }

    for (const c of plan.columns) {
      if (c.referencesTable) fks.push(addForeignKey(plan.table, c));
    }
    for (const idx of plan.indexes) indexes.push(createIndex(plan.table, idx));
  }

  return { additive: [...creates, ...alters, ...fks, ...indexes], destructive };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm test src/lib/db/__tests__/diff.test.ts`
Expected: PASS (all four cases).

- [ ] **Step 5: Commit**

```bash
git add src/lib/db/diff.ts src/lib/db/__tests__/diff.test.ts
git commit -m "feat(db): additive-safe schema diff (destructive → report, never applied)"
```

---

## Task 7: reconcile — orchestrate apply in a transaction

**Files:**
- Create: `src/lib/db/reconcile.ts`
- Test: `src/lib/db/__tests__/reconcile.test.ts`

- [ ] **Step 1: Write failing tests (pglite end-to-end of the lib)**

```ts
import { describe, it, expect, beforeEach } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { reconcile } from "@/lib/db/reconcile";
import { introspect } from "@/lib/db/introspect";
import type { EntityDef } from "@/lib/entities/registry";

const lip = (extra = {}): EntityDef => ({
  name: "Lipstick",
  access: "public",
  fields: { name: { type: "string" }, price: { type: "number" }, ...extra },
});

async function freshDb() {
  const db = new PGlite();
  // users table must exist (created_by FK target); mirrors auth bootstrap.
  await db.exec(`CREATE TABLE users (id uuid PRIMARY KEY DEFAULT gen_random_uuid());`);
  return db;
}

describe("reconcile", () => {
  it("creates a real table with entity + service columns", async () => {
    const db = await freshDb();
    const report = await reconcile(db, [lip()]);
    expect(report.destructive).toEqual([]);
    const cur = await introspect(db);
    const cols = [...(cur.get("lipstick")?.keys() ?? [])].sort();
    expect(cols).toEqual(["created_at", "created_by", "id", "name", "price", "updated_at"]);
  });

  it("is idempotent (re-run = no error, same shape)", async () => {
    const db = await freshDb();
    await reconcile(db, [lip()]);
    await reconcile(db, [lip()]); // must not throw
    const cur = await introspect(db);
    expect(cur.has("lipstick")).toBe(true);
  });

  it("adds a new field as a column without touching existing rows", async () => {
    const db = await freshDb();
    await reconcile(db, [lip()]);
    await db.exec(`INSERT INTO lipstick (name, price, created_by) SELECT 'Rouge', 1490, id FROM users LIMIT 1;`);
    await db.exec(`INSERT INTO users DEFAULT VALUES;`);
    await reconcile(db, [lip({ shade: { type: "string" } })]);
    const cur = await introspect(db);
    expect(cur.get("lipstick")?.has("shade")).toBe(true);
    const r = await db.query<{ n: string }>(`SELECT count(*)::text AS n FROM lipstick`);
    expect(r.rows[0].n).toBe("1"); // row survived
  });

  it("reports a removed field instead of dropping it", async () => {
    const db = await freshDb();
    await reconcile(db, [lip({ legacy: { type: "string" } })]);
    const report = await reconcile(db, [lip()]); // legacy gone from declaration
    expect(report.destructive).toContainEqual({ table: "lipstick", column: "legacy", reason: "removed-field" });
    const cur = await introspect(db);
    expect(cur.get("lipstick")?.has("legacy")).toBe(true); // NOT dropped
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test src/lib/db/__tests__/reconcile.test.ts`
Expected: FAIL — cannot find module `@/lib/db/reconcile`.

- [ ] **Step 3: Implement reconcile.ts**

```ts
/**
 * Orchestrates additive-safe materialization: introspect → diff → apply the
 * additive DDL inside a single transaction. Destructive diffs are returned in
 * the report, never executed. Caller supplies the EntityDefs (boot script loads
 * them from entities/*.json). Pure of any global state; takes a QueryRunner.
 */
import type { EntityDef } from "@/lib/entities/registry";
import { tablePlan } from "@/lib/db/tableplan";
import { diffSchema, type DestructiveItem } from "@/lib/db/diff";
import { introspect, type QueryRunner } from "@/lib/db/introspect";

export interface ReconcileReport {
  applied: number;
  destructive: DestructiveItem[];
}

export async function reconcile(q: QueryRunner, defs: EntityDef[]): Promise<ReconcileReport> {
  const plans = defs.map(tablePlan);
  const current = await introspect(q);
  const { additive, destructive } = diffSchema(plans, current);

  if (additive.length) {
    await q.query("BEGIN");
    try {
      for (const sql of additive) await q.query(sql);
      await q.query("COMMIT");
    } catch (err) {
      await q.query("ROLLBACK");
      throw err;
    }
  }
  return { applied: additive.length, destructive };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm test src/lib/db/__tests__/reconcile.test.ts`
Expected: PASS (4 cases). Note: PGlite runs statements one-per-`query`; the `DO $$…$$` FK block executes as a single statement — fine.

- [ ] **Step 5: Commit**

```bash
git add src/lib/db/reconcile.ts src/lib/db/__tests__/reconcile.test.ts
git commit -m "feat(db): transactional additive reconciler (entities → real tables)"
```

---

## Task 8: codegen — the explicit Drizzle artifact

**Files:**
- Create: `src/lib/db/codegen-schema.ts`
- Test: `src/lib/db/__tests__/codegen-schema.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
import { describe, it, expect } from "vitest";
import { generateEntitySchemaSource } from "@/lib/db/codegen-schema";
import type { EntityDef } from "@/lib/entities/registry";

const defs: EntityDef[] = [
  { name: "Lipstick", access: "public", fields: { name: { type: "string" }, price: { type: "number" }, inStock: { type: "boolean" } } },
  { name: "CartItem", access: "owner", fields: { lipstickId: { type: "reference", entity: "Lipstick" }, quantity: { type: "number" } } },
];

describe("generateEntitySchemaSource", () => {
  const src = generateEntitySchemaSource(defs);
  it("emits a pgTable per entity with snake table + column names", () => {
    expect(src).toContain(`export const lipstick = pgTable("lipstick"`);
    expect(src).toContain(`name: text("name")`);
    expect(src).toContain(`price: numeric("price")`);
    expect(src).toContain(`inStock: boolean("in_stock")`);
    expect(src).toContain(`export const cartItem = pgTable("cart_item"`);
    expect(src).toContain(`lipstickId: uuid("lipstick_id")`);
  });
  it("includes a generated-file banner so nobody hand-edits it", () => {
    expect(src).toContain("GENERATED");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test src/lib/db/__tests__/codegen-schema.test.ts`
Expected: FAIL — cannot find module `@/lib/db/codegen-schema`.

- [ ] **Step 3: Implement codegen-schema.ts**

```ts
/**
 * Emits src/lib/db/entity-schema.ts — idiomatic Drizzle table defs mirroring
 * entities/*.json. This is the "явный" (explicit) artifact: the dev reads it,
 * it travels with the exported code, and AI-written custom server code can
 * import typed tables. It is GENERATED (reconciler owns the real DDL); never
 * hand-edited. Auth tables stay in schema.ts. Pure module (returns a string).
 */
import type { EntityDef, FieldDef } from "@/lib/entities/registry";
import { snakeCase } from "@/lib/db/typemap";

function drizzleCol(fieldName: string, f: FieldDef): string {
  const col = snakeCase(fieldName);
  const ts = camelIdent(fieldName);
  switch (f.type) {
    case "number":
      return `  ${ts}: numeric(${q(col)}),`;
    case "boolean":
      return `  ${ts}: boolean(${q(col)}),`;
    case "date":
      return `  ${ts}: timestamp(${q(col)}, { withTimezone: true }),`;
    case "reference":
      return `  ${ts}: uuid(${q(col)}),`;
    case "string":
    case "text":
    case "enum":
    default:
      return `  ${ts}: text(${q(col)}),`;
  }
}

function camelIdent(name: string): string {
  // Already a safe identifier; keep as-is for the TS property name.
  return name;
}
function q(s: string): string {
  return `"${s}"`;
}

export function generateEntitySchemaSource(defs: EntityDef[]): string {
  const header = [
    "// GENERATED by src/lib/db/codegen-schema.ts from entities/*.json — DO NOT EDIT.",
    "// Real DDL is owned by the reconciler; this file is the typed/explicit view.",
    `import { pgTable, text, numeric, boolean, timestamp, uuid } from "drizzle-orm/pg-core";`,
    "",
  ];
  const blocks = defs.map((def) => {
    const table = snakeCase(def.name);
    const ident = table.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
    const cols = Object.entries(def.fields).map(([k, f]) => drizzleCol(k, f));
    return [
      `export const ${ident} = pgTable(${q(table)}, {`,
      `  id: uuid("id").primaryKey().defaultRandom(),`,
      ...cols,
      `  createdBy: uuid("created_by").notNull(),`,
      `  createdAt: timestamp("created_at", { withTimezone: true }).notNull(),`,
      `  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull(),`,
      `});`,
      "",
    ].join("\n");
  });
  return [...header, ...blocks].join("\n");
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm test src/lib/db/__tests__/codegen-schema.test.ts`
Expected: PASS. (Note: `cartItem` ident derived from `cart_item` → `cartItem`.)

- [ ] **Step 5: Commit**

```bash
git add src/lib/db/codegen-schema.ts src/lib/db/__tests__/codegen-schema.test.ts
git commit -m "feat(db): codegen explicit entity-schema.ts (Drizzle, generated)"
```

---

## Task 9: boot entry + flag wiring

**Files:**
- Create: `scripts/reconcile.ts`
- Modify: `docker-entrypoint.sh`

- [ ] **Step 1: Implement scripts/reconcile.ts**

```ts
/**
 * Boot/CLI entry: load entities/*.json, reconcile real tables, write the
 * generated entity-schema.ts, log the report. Run via `tsx scripts/reconcile.ts`
 * AFTER init-db.mjs (which creates the users table the FKs target). Fail-soft:
 * any error logs and exits 0 so the dev server still starts.
 */
import { promises as fs } from "node:fs";
import path from "node:path";
import pg from "pg";
import { listEntities, loadEntity, type EntityDef } from "@/lib/entities/registry";
import { reconcile } from "@/lib/db/reconcile";
import { generateEntitySchemaSource } from "@/lib/db/codegen-schema";

async function main() {
  const url = process.env.DATABASE_URL;
  if (!url) {
    console.log("[reconcile] no DATABASE_URL — skipping");
    return;
  }
  const names = await listEntities();
  const defs = (await Promise.all(names.map(loadEntity))).filter((d): d is EntityDef => !!d);
  if (!defs.length) {
    console.log("[reconcile] no entities — nothing to materialize");
    return;
  }
  const pool = new pg.Pool({ connectionString: url, max: 1, connectionTimeoutMillis: 15000 });
  try {
    const report = await reconcile(pool, defs);
    console.log(`[reconcile] applied=${report.applied} destructive=${report.destructive.length}`);
    for (const d of report.destructive) {
      console.warn(`[reconcile] DESTRUCTIVE (skipped): ${d.table}.${d.column} — ${d.reason}`);
    }
    const src = generateEntitySchemaSource(defs);
    await fs.writeFile(path.join(process.cwd(), "src/lib/db/entity-schema.ts"), src, "utf8");
  } finally {
    await pool.end().catch(() => {});
  }
}

main().catch((err) => {
  console.error("[reconcile] failed (non-fatal):", err?.message ?? err);
  process.exitCode = 0;
});
```

- [ ] **Step 2: Wire into docker-entrypoint.sh (flag-gated, fail-soft)**

Find the existing `init-db.mjs` invocation line. Immediately AFTER it, add:

```sh
if [ "$ENTITY_REAL_TABLES" = "true" ]; then
  echo "[entrypoint] ENTITY_REAL_TABLES on — reconciling entity tables"
  timeout 60 pnpm exec tsx scripts/reconcile.ts || echo "[entrypoint] reconcile slow/failed — starting dev anyway"
fi
```

- [ ] **Step 3: Verify the script typechecks + runs against pglite-less local Postgres OR is dry-run safe**

Run: `cd apps/orchestrator/templates/nextjs-entities && pnpm exec tsc --noEmit -p tsconfig.json`
Expected: PASS (no type errors). If `tsconfig` lacks `scripts/`, add `"scripts/**/*.ts"` to its `include`.

- [ ] **Step 4: Full test suite green**

Run: `pnpm test`
Expected: PASS (smoke + typemap + tableplan + ddl + introspect + diff + reconcile + codegen).

- [ ] **Step 5: Commit**

```bash
git add scripts/reconcile.ts docker-entrypoint.sh tsconfig.json
git commit -m "feat(db): boot reconcile entry + ENTITY_REAL_TABLES flag (fail-soft, dev path)"
```

---

## Task 10: push + verify on a real project container (manual)

**Files:** none (verification).

- [ ] **Step 1: Commit any lockfile drift + push**

```bash
git add apps/orchestrator/templates/nextjs-entities/pnpm-lock.yaml
git commit -m "chore: lockfile for db materialization tooling" || true
git push origin main
```

- [ ] **Step 2: Verify on a throwaway entity project (real Postgres)**

Provision a fresh `nextjs_entities` project with `ENTITY_REAL_TABLES=true` in its env, give it a `Lipstick` + `CartItem` entity, boot the container, then from the shared user-postgres confirm real tables exist:

Run (on the VPS, against the project schema): `\dt` then `\d lipstick`
Expected: tables `lipstick`, `cart_item` exist with typed columns + `*_created_by_idx` / `*_lipstick_id_idx` indexes + a `cart_item_lipstick_id_fkey` FK. `entity-schema.ts` present in the project.

- [ ] **Step 3: Verify additive evolution + destructive report**

Add a field to `Lipstick.json`, re-run `pnpm exec tsx scripts/reconcile.ts` in the container: the column appears, existing rows intact. Remove a field, re-run: log shows `DESTRUCTIVE (skipped)`, column kept.

---

## Self-Review

**Spec coverage:**
- Real per-entity tables from entities → Tasks 2–7, 9 (typemap/tableplan/ddl/diff/reconcile + boot). ✓
- Additive-safe evolution (auto-add; destructive→report) → Task 6 (diff) + Task 7 (reconcile) tests. ✓
- Hybrid materialization (reconciler DDL + codegen schema.ts) → Task 7 (DDL) + Task 8 (codegen `entity-schema.ts`, separate from auth `schema.ts`). ✓
- Type mapping (string/text/number/boolean/date/enum→text+CHECK/reference→uuid+FK) → Task 2 typemap + Task 4 ddl. ✓
- Indexes (created_by + reference cols) → Task 3 tableplan + Task 4/6. ✓
- Flag `ENTITY_REAL_TABLES`, no breakage of existing apps → Task 9 (gated; engine still on `records` until Plan 2; default off). ✓
- Fail-safe (additive-only, transactional, idempotent, fail-soft boot) → Task 4 (`IF NOT EXISTS`), Task 7 (txn), Task 9 (timeout + exit 0). ✓
- Tests (unit pure + integration via pglite + manual E2E) → every task + Task 10. ✓
- Out of scope respected: engine rewrite, orchestrator hot_reload, records→tables migrator, prod build path → all deferred to Plan 2 (stated in header). ✓

**Placeholder scan:** none — every step has real code/commands/expected output.

**Type consistency:** `ColumnSpec` (typemap) used by tableplan/ddl/diff; `TablePlan`/`SERVICE_COLUMNS` (tableplan) used by ddl/diff; `QueryRunner`/`CurrentSchema` (introspect) used by diff/reconcile; `DestructiveItem` (diff) used by reconcile; `EntityDef`/`FieldDef`/`listEntities`/`loadEntity` imported from existing `registry.ts` (verified shapes). `reconcile(q, defs)` signature consistent between Task 7 and Task 9. ✓

---

## Notes for the engineer

- `enum` becomes `text` + a CHECK only at CREATE TABLE; `ADD COLUMN` deliberately omits the CHECK (avoids scanning/locking an existing table). Enum validity is already enforced by `registry.createSchema` (zod) at the app layer.
- Entity columns are nullable in the DB on purpose (additive-safe). "Required" is an app-layer rule, not a DB constraint — so an `ADD COLUMN` on a table with existing rows can never fail.
- FKs use `ON DELETE SET NULL` (column nullable) — deleting a referenced row blanks the link rather than cascade-deleting.
- This plan does NOT make the running app use the new tables. After it lands, Plan 2 rewrites `src/lib/entities/engine.ts` to read/write per-entity tables, wires the orchestrator `hot_reload` to run `scripts/reconcile.ts` on entity changes, adds the records→tables migrator, and the prod (`next build`) reconcile path.
