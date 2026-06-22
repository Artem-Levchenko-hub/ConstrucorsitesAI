/**
 * CRUD engine — the ONE place that touches the `records` table. Every read and
 * write goes through here so auth, owner-scoping, validation and pagination are
 * enforced centrally. AI-generated frontend code never queries the DB; it calls
 * the SDK → the route handlers → these functions. That's the whole safety model:
 * the model cannot forget owner-scoping because it never writes the query.
 *
 * Fixed template file — the AI never edits it.
 */

import { and, asc, desc, eq, inArray, sql, type SQL } from "drizzle-orm";

import { db } from "@/lib/db";
import { records } from "@/lib/db/schema";
import type { CurrentUser } from "@/lib/session";
import {
  applyDefaults,
  createSchema,
  fieldSqlType,
  listEntities,
  loadEntity,
  referenceFields,
  updateSchema,
  type EntityDef,
} from "@/lib/entities/registry";

/** A drizzle executor — either the pooled `db` or a transaction handle `tx`.
 *  Both expose the same select/insert/update/delete builder, so integrity helpers
 *  run identically inside or outside a transaction. */
type Executor = typeof db | Parameters<Parameters<typeof db.transaction>[0]>[0];

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

const RESERVED = new Set(["sort", "order", "limit", "offset", "page", "expand"]);
// Per-request hard cap. The SDK auto-paginates at this size to pull a whole
// collection in as few round-trips as possible (see lib/sdk fetchAll), so it
// must match the SDK's AUTO_PAGE. A bounded explicit caller still can't ask for
// more than this in one shot.
const MAX_LIMIT = 500;
// Window for an explicit caller that passes no `limit`. The SDK never relies on
// this (it sends `limit` on every auto-page request); it only bounds a hand-rolled
// fetch that forgot to page.
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
  // Named-role overlay (multi-role apps: teacher/student/parent, doctor/patient).
  // Runs FIRST and is stricter than `access`: an entity that restricts a mode to
  // specific roles is no longer reachable by other roles — and a `public` entity
  // with `readRoles` stops being anonymously readable. `admin` is the app operator
  // and always passes (it manages everything), mirroring maskPrivate.
  const roleGate = mode === "read" ? def.readRoles : def.writeRoles;
  if (roleGate && roleGate.length) {
    if (!user) throw new EngineError(401, "authentication required");
    if (user.role !== "admin" && !roleGate.includes(user.role))
      throw new EngineError(403, "your role can't do this");
  }

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
  // A `date` field's write validator only checks JS `Date.parse` (lenient),
  // but a raw `::timestamptz` cast uses Postgres' stricter parser — so a single
  // JS-valid-but-PG-invalid value ("2025", "June 2025", "2025-02-30") would
  // throw and 500 the WHOLE list the moment anyone sorts by that column. Sort
  // through a crash-proof cast so one bad row can never deny the view; poison
  // values become NULL (sort to the NULL end) instead of erroring.
  if (t === "date") return sql`safe_to_timestamptz(${records.data} ->> ${field})`;
  return sql`${records.data} ->> ${field}`;
}

function shape(row: typeof records.$inferSelect): Record<string, unknown> {
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

/**
 * Strip `private`-marked fields from a shaped row for a reader who isn't allowed
 * to see them. Only bites on `public` entities (owner/admin entities are read
 * only by the owner/admin anyway): a public directory can declare `phone`/`email`
 * private and the engine drops them for everyone except the row's author/admin —
 * so anonymous reads (and scrapers) never receive the contact data.
 */
function maskPrivate(
  def: EntityDef,
  user: CurrentUser | null,
  row: Record<string, unknown>,
): Record<string, unknown> {
  if (def.access !== "public") return row;
  const priv = Object.entries(def.fields)
    .filter(([, f]) => f.private)
    .map(([k]) => k);
  if (!priv.length) return row;
  const isPrivileged =
    !!user && (user.role === "admin" || row.created_by === user.id);
  if (isPrivileged) return row;
  const out = { ...row };
  for (const k of priv) delete out[k];
  return out;
}

/** Non-throwing access for an EXPAND fetch: skip (null) instead of erroring. */
function scopeForExpand(
  targetDef: EntityDef,
  user: CurrentUser | null,
): { allowed: boolean; scopeOwner: boolean } {
  // A read-role-gated relation must not leak through ?expand to a reader who
  // couldn't read it directly. Admin bypasses (operator sees all), same as authorize.
  if (targetDef.readRoles && targetDef.readRoles.length) {
    const ok = !!user && (user.role === "admin" || targetDef.readRoles.includes(user.role));
    if (!ok) return { allowed: false, scopeOwner: false };
  }
  if (targetDef.access === "admin")
    return { allowed: user?.role === "admin", scopeOwner: false };
  if (targetDef.access === "public") return { allowed: true, scopeOwner: false };
  return { allowed: !!user, scopeOwner: true }; // owner
}

/**
 * Embed related records for the requested `reference` fields into each row's
 * `_expanded` map. Batched (one query per target entity, `id = ANY(...)`) so a
 * list of N rows never does N+1. Each expand fetch is access-scoped exactly like
 * a normal read of the target entity — an inaccessible relation comes back null,
 * never leaking another user's row.
 */
async function expandRecords(
  def: EntityDef,
  rows: Record<string, unknown>[],
  expandFields: string[],
  user: CurrentUser | null,
): Promise<Record<string, unknown>[]> {
  if (!rows.length || !expandFields.length) return rows;
  const refs = referenceFields(def);
  const fields = expandFields.filter((f) => refs[f]);
  if (!fields.length) return rows;

  for (const field of fields) {
    const targetDef = await loadEntity(refs[field]);
    let map = new Map<string, Record<string, unknown>>();
    if (targetDef) {
      const scope = scopeForExpand(targetDef, user);
      const ids = [
        ...new Set(
          rows
            .map((r) => r[field])
            .filter((v): v is string => typeof v === "string" && v.length > 0),
        ),
      ];
      if (scope.allowed && ids.length) {
        const conds: SQL[] = [
          eq(records.entity, targetDef.name),
          inArray(records.id, ids),
        ];
        if (scope.scopeOwner && user) conds.push(eq(records.createdBy, user.id));
        const rel = await db.select().from(records).where(and(...conds));
        map = new Map(rel.map((r) => [r.id, maskPrivate(targetDef, user, shape(r))]));
      }
    }
    for (const r of rows) {
      const exp = (r._expanded as Record<string, unknown>) ?? {};
      const refId = r[field];
      exp[field] = typeof refId === "string" ? map.get(refId) ?? null : null;
      r._expanded = exp;
    }
  }
  return rows;
}

/** Parse `?expand=a,b` (or an explicit string[]) into a clean field list. */
function parseExpand(raw: string | string[] | null | undefined): string[] {
  if (!raw) return [];
  const arr = Array.isArray(raw) ? raw : raw.split(",");
  return arr.map((s) => s.trim()).filter(Boolean);
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

  const shaped = rows.map(shape).map((r) => maskPrivate(def, user, r));
  const expand = parseExpand(params.get("expand"));
  return expand.length ? await expandRecords(def, shaped, expand, user) : shaped;
}

/** Reject a write whose `unique` field value duplicates another row's. Shared by
 *  create and update; `excludeId` skips the row being updated so re-saving it
 *  unchanged isn't a false duplicate. Scoped like reads (owner rows for `owner`
 *  entities, global for `public`/`admin`); case-insensitive for strings.
 *  NB: app-level guard, not a DB constraint — two concurrent creates can still
 *  race past it (TOCTOU); good enough for the single-owner CRUD this serves. */
async function assertUnique(
  def: EntityDef,
  owner: CurrentUser,
  values: Record<string, unknown>,
  excludeId?: string,
) {
  for (const [key, f] of Object.entries(def.fields)) {
    if (!f.unique) continue;
    const val = values[key];
    if (val === undefined || val === null || val === "") continue;
    const dupConds: SQL[] = [
      eq(records.entity, def.name),
      sql`lower(${records.data} ->> ${key}) = lower(${String(val)})`,
    ];
    if (def.access === "owner") dupConds.push(eq(records.createdBy, owner.id));
    if (excludeId) dupConds.push(sql`${records.id} <> ${excludeId}`);
    const [dup] = await db
      .select({ id: records.id })
      .from(records)
      .where(and(...dupConds))
      .limit(1);
    if (dup) throw new EngineError(409, `Такое значение поля «${key}» уже есть`);
  }
}

/** Reject a write that points a `reference` field at a non-existent target row, so
 *  a record can't be saved with a dangling relation. This is the create/update half
 *  of referential integrity (the delete half is `applyOnDelete`). Empty/optional refs
 *  are allowed; a reference to an UNKNOWN entity type is skipped — that's a schema
 *  issue, not bad data, and we never hard-block a write on a misconfigured schema. */
async function assertReferencesExist(
  def: EntityDef,
  values: Record<string, unknown>,
) {
  const refs = referenceFields(def);
  for (const [field, target] of Object.entries(refs)) {
    const val = values[field];
    if (typeof val !== "string" || val.length === 0) continue;
    if (!(await loadEntity(target))) continue; // unknown target type — skip, don't block
    const [hit] = await db
      .select({ id: records.id })
      .from(records)
      .where(and(eq(records.entity, target), eq(records.id, val)))
      .limit(1);
    if (!hit)
      throw new EngineError(400, `Связанная запись в поле «${field}» не найдена`);
  }
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

  // Integrity guards (shared with updateRecord): no duplicate `unique` value, and
  // every `reference` must point at a row that actually exists.
  await assertUnique(def, owner, data);
  await assertReferencesExist(def, data);

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
  expand?: string[];
}) {
  const row = await findScoped(opts.def, opts.user, opts.id);
  if (!row) throw new EngineError(404, "not found");
  const shaped = maskPrivate(opts.def, opts.user, shape(row));
  const expand = parseExpand(opts.expand);
  if (!expand.length) return shaped;
  const [out] = await expandRecords(opts.def, [shaped], expand, opts.user);
  return out;
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

  // Optimistic concurrency: if the caller sends the `_updatedAt` it loaded the
  // row at, refuse the write when the row has changed since — so two people
  // editing the same record don't silently clobber each other (last-write-wins).
  // Opt-in: a payload without `_updatedAt` keeps the old behaviour, no regression.
  if (opts.body && typeof opts.body === "object") {
    const expected = (opts.body as Record<string, unknown>)._updatedAt;
    if (expected !== undefined && expected !== null && expected !== "") {
      const exp = new Date(String(expected)).getTime();
      const cur = new Date(existing[0].updatedAt as unknown as string).getTime();
      if (!Number.isNaN(exp) && !Number.isNaN(cur) && exp !== cur) {
        throw new EngineError(
          409,
          "Запись изменена другим пользователем — обновите страницу и повторите",
        );
      }
    }
  }

  const merged = { ...(existing[0].data as Record<string, unknown>), ...parsed.data };

  // Same integrity guards as create: uniqueness (excluding THIS row so an unchanged
  // re-save isn't a false duplicate) and reference existence on the merged values.
  await assertUnique(def, owner, merged, id);
  await assertReferencesExist(def, merged);

  const [row] = await db
    .update(records)
    .set({ data: merged, updatedAt: sql`now()` })
    .where(and(...conds))
    .returning();
  return shape(row);
}

/** Other entities that hold a `reference` field pointing AT `targetName`, each
 *  paired with the field name and its onDelete policy. This is the reverse of
 *  referenceFields — "who points at me", needed to fix dangling links on delete. */
async function referencingFields(
  targetName: string,
): Promise<{ def: EntityDef; field: string; policy: "setNull" | "cascade" | "restrict" }[]> {
  const out: { def: EntityDef; field: string; policy: "setNull" | "cascade" | "restrict" }[] = [];
  for (const name of await listEntities()) {
    const d = await loadEntity(name);
    if (!d) continue;
    for (const [field, f] of Object.entries(d.fields)) {
      if (f.type === "reference" && f.entity === targetName) {
        out.push({ def: d, field, policy: f.onDelete ?? "setNull" });
      }
    }
  }
  return out;
}

// Safety cap on a single cascade closure so a mis-modelled cycle/fan-out can't
// delete an unbounded number of rows in one request.
const CASCADE_MAX = 5000;

/**
 * Keep referential integrity when rows `parentIds` of entity `parentName` are
 * removed: for every entity that references them, apply that reference's policy —
 * `setNull` clears the dangling pointer, `cascade` deletes the child (and recurses
 * into ITS children), `restrict` aborts the whole delete (throws 409). Runs inside
 * the caller's transaction, so a `restrict` deeper in the graph rolls back every
 * earlier setNull/cascade — the delete is all-or-nothing. `removed` dedupes ids
 * across recursion so a reference cycle terminates.
 */
async function applyOnDelete(
  tx: Executor,
  parentName: string,
  parentIds: string[],
  removed: Set<string>,
): Promise<void> {
  if (!parentIds.length) return;
  for (const { def: childDef, field, policy } of await referencingFields(parentName)) {
    const kids = await tx
      .select()
      .from(records)
      .where(
        and(eq(records.entity, childDef.name), inArray(sql`(${records.data} ->> ${field})`, parentIds)),
      );
    if (!kids.length) continue;
    if (policy === "restrict") {
      throw new EngineError(409, `Нельзя удалить: на запись ссылаются «${childDef.name}»`);
    }
    if (policy === "setNull") {
      for (const k of kids) {
        const data = { ...(k.data as Record<string, unknown>), [field]: null };
        await tx.update(records).set({ data, updatedAt: sql`now()` }).where(eq(records.id, k.id));
      }
      continue;
    }
    // cascade
    const kidIds = kids.map((k) => k.id).filter((kid) => !removed.has(kid));
    if (!kidIds.length) continue;
    if (removed.size + kidIds.length > CASCADE_MAX) {
      throw new EngineError(409, "слишком много связанных записей для каскадного удаления");
    }
    kidIds.forEach((kid) => removed.add(kid));
    await tx.delete(records).where(inArray(records.id, kidIds));
    await applyOnDelete(tx, childDef.name, kidIds, removed); // grandchildren
  }
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

  // One transaction so referential integrity (setNull / cascade / restrict on
  // every entity that references this row) and the delete itself are atomic — a
  // `restrict` anywhere in the graph aborts the whole thing, never a half-delete.
  const removed = await db.transaction(async (tx) => {
    const [target] = await tx
      .select({ id: records.id })
      .from(records)
      .where(and(...conds))
      .limit(1);
    if (!target) throw new EngineError(404, "not found");
    const seen = new Set<string>([id]);
    await applyOnDelete(tx, def.name, [id], seen);
    await tx.delete(records).where(and(...conds));
    return seen;
  });
  // `seen` counts the parent + every cascaded descendant actually deleted.
  return { id, deleted: true, removed: removed.size };
}
