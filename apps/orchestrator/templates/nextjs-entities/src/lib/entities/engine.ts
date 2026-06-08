/**
 * CRUD engine — the ONE place that touches the `records` table. Every read and
 * write goes through here so auth, owner-scoping, validation and pagination are
 * enforced centrally. AI-generated frontend code never queries the DB; it calls
 * the SDK → the route handlers → these functions. That's the whole safety model:
 * the model cannot forget owner-scoping because it never writes the query.
 *
 * Fixed template file — the AI never edits it.
 */

import { and, asc, desc, eq, sql, type SQL } from "drizzle-orm";

import { db } from "@/lib/db";
import { records } from "@/lib/db/schema";
import type { CurrentUser } from "@/lib/session";
import {
  applyDefaults,
  createSchema,
  fieldSqlType,
  updateSchema,
  type EntityDef,
} from "@/lib/entities/registry";

/** Carries an HTTP status so route handlers map failures cleanly. */
export class EngineError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "EngineError";
  }
}

const RESERVED = new Set(["sort", "order", "limit", "offset", "page"]);
const MAX_LIMIT = 200;
const DEFAULT_LIMIT = 50;

/**
 * Resolve what the caller is allowed to do and whether reads/writes must be
 * scoped to their own rows. Throws EngineError(401/403) when not allowed.
 *
 *  - owner  : auth required; every read+write scoped to created_by = me.
 *  - public : reads open to anyone (even anonymous); writes need auth and are
 *             still scoped to the author (you can only edit your own rows).
 *  - admin  : role "admin" required for everything; not owner-scoped.
 */
function authorize(
  def: EntityDef,
  user: CurrentUser | null,
  mode: "read" | "write",
): { scopeOwner: boolean } {
  if (def.access === "admin") {
    if (!user) throw new EngineError(401, "authentication required");
    if (user.role !== "admin") throw new EngineError(403, "admin only");
    return { scopeOwner: false };
  }
  if (def.access === "public") {
    if (mode === "read") return { scopeOwner: false };
    if (!user) throw new EngineError(401, "authentication required");
    return { scopeOwner: true };
  }
  // owner (default)
  if (!user) throw new EngineError(401, "authentication required");
  return { scopeOwner: true };
}

/** Build the `data->>field` extraction with the cast the field's type needs. */
function fieldExpr(def: EntityDef, field: string): SQL | null {
  const t = fieldSqlType(def, field);
  if (!t) return null;
  if (t === "number") return sql`(${records.data} ->> ${field})::numeric`;
  if (t === "date") return sql`(${records.data} ->> ${field})::timestamptz`;
  return sql`${records.data} ->> ${field}`;
}

function shape(row: typeof records.$inferSelect) {
  // Flatten to the Base44-ish record shape the SDK/UI expects: the entity's
  // own fields, then engine metadata LAST so id/timestamps always win even if a
  // (mis)declared field collides with a reserved name.
  return {
    ...(row.data as Record<string, unknown>),
    id: row.id,
    created_by: row.createdBy,
    created_at: row.createdAt,
    updated_at: row.updatedAt,
  };
}

export async function listRecords(opts: {
  def: EntityDef;
  user: CurrentUser | null;
  params: URLSearchParams;
}) {
  const { def, user, params } = opts;
  const { scopeOwner } = authorize(def, user, "read");

  const conds: SQL[] = [eq(records.entity, def.name)];
  if (scopeOwner && user) conds.push(eq(records.createdBy, user.id));

  // Equality filters on whitelisted fields only — unknown keys are ignored.
  for (const [key, value] of params.entries()) {
    if (RESERVED.has(key)) continue;
    const expr = fieldExpr(def, key);
    if (!expr) continue;
    conds.push(sql`${records.data} ->> ${key} = ${value}`);
  }

  // Sort: a whitelisted field (typed cast) or created_at by default.
  const sortField = params.get("sort");
  const dir = params.get("order") === "asc" ? "asc" : "desc";
  let orderExpr: SQL;
  const fe = sortField ? fieldExpr(def, sortField) : null;
  orderExpr = fe ?? sql`${records.createdAt}`;
  const order = dir === "asc" ? asc(orderExpr) : desc(orderExpr);

  const limit = Math.min(
    MAX_LIMIT,
    Math.max(1, Number(params.get("limit")) || DEFAULT_LIMIT),
  );
  const page = Math.max(1, Number(params.get("page")) || 1);
  const offset = Number(params.get("offset")) || (page - 1) * limit;

  const rows = await db
    .select()
    .from(records)
    .where(and(...conds))
    .orderBy(order)
    .limit(limit)
    .offset(Math.max(0, offset));

  return rows.map(shape);
}

export async function createRecord(opts: {
  def: EntityDef;
  user: CurrentUser | null;
  body: unknown;
}) {
  const { def, user } = opts;
  authorize(def, user, "write");
  // user is guaranteed non-null after a successful write authorize.
  const owner = user as CurrentUser;

  const parsed = createSchema(def).safeParse(opts.body);
  if (!parsed.success) {
    throw new EngineError(400, parsed.error.issues.map((i) => i.message).join("; "));
  }
  const data = applyDefaults(def, parsed.data as Record<string, unknown>);

  const [row] = await db
    .insert(records)
    .values({ entity: def.name, data, createdBy: owner.id })
    .returning();
  return shape(row);
}

/** Shared row lookup honouring entity + ownership scope. */
async function findScoped(def: EntityDef, user: CurrentUser | null, id: string) {
  const { scopeOwner } = authorize(def, user, "read");
  const conds: SQL[] = [eq(records.id, id), eq(records.entity, def.name)];
  if (scopeOwner && user) conds.push(eq(records.createdBy, user.id));
  const [row] = await db
    .select()
    .from(records)
    .where(and(...conds))
    .limit(1);
  return row ?? null;
}

export async function getRecord(opts: {
  def: EntityDef;
  user: CurrentUser | null;
  id: string;
}) {
  const row = await findScoped(opts.def, opts.user, opts.id);
  if (!row) throw new EngineError(404, "not found");
  return shape(row);
}

export async function updateRecord(opts: {
  def: EntityDef;
  user: CurrentUser | null;
  id: string;
  body: unknown;
}) {
  const { def, user, id } = opts;
  authorize(def, user, "write");
  const owner = user as CurrentUser;

  const parsed = updateSchema(def).safeParse(opts.body);
  if (!parsed.success) {
    throw new EngineError(400, parsed.error.issues.map((i) => i.message).join("; "));
  }

  // Writes are always owner-scoped (even for public entities you edit your own).
  const conds: SQL[] = [
    eq(records.id, id),
    eq(records.entity, def.name),
    eq(records.createdBy, owner.id),
  ];
  const existing = await db
    .select()
    .from(records)
    .where(and(...conds))
    .limit(1);
  if (!existing[0]) throw new EngineError(404, "not found");

  const merged = { ...(existing[0].data as Record<string, unknown>), ...parsed.data };
  const [row] = await db
    .update(records)
    .set({ data: merged, updatedAt: sql`now()` })
    .where(and(...conds))
    .returning();
  return shape(row);
}

export async function deleteRecord(opts: {
  def: EntityDef;
  user: CurrentUser | null;
  id: string;
}) {
  const { def, user, id } = opts;
  authorize(def, user, "write");
  const owner = user as CurrentUser;

  const conds: SQL[] = [
    eq(records.id, id),
    eq(records.entity, def.name),
    eq(records.createdBy, owner.id),
  ];
  const deleted = await db
    .delete(records)
    .where(and(...conds))
    .returning({ id: records.id });
  if (!deleted[0]) throw new EngineError(404, "not found");
  return { id: deleted[0].id, deleted: true };
}
