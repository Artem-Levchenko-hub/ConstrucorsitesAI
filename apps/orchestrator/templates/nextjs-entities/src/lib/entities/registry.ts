/**
 * Entity registry — the bridge between `entities/<Name>.json` schema files and
 * the CRUD engine. The AI defines entities as JSON; this module loads them,
 * builds zod validators, and exposes the access policy + the field whitelists
 * the engine uses for safe filter/sort.
 *
 * WHY read from disk per request (not `import`): in the dev container the AI
 * edits `entities/*.json` live via the orchestrator (docker cp). A static
 * `import`/`import()` would be cached in the bundler module graph for the whole
 * dev-server lifetime and keep serving the OLD schema. We `fs.readFile` instead,
 * skipping the read only while the file's mtime is unchanged — so edits take
 * effect on the very next request, with no container restart.
 *
 * This file is part of the fixed template. The AI never edits it.
 */

import { promises as fs } from "fs";
import path from "path";
import { z } from "zod";

/** Supported field types in an entity schema. Kept deliberately small. */
export type FieldType =
  | "string"
  | "text"
  | "number"
  | "boolean"
  | "date"
  // A calendar day + clock time (ISO 8601, e.g. 2026-06-21T14:30). Use this — not
  // `date` — whenever the time of day matters (appointments, visits, shifts):
  // `date` alone drops 10:00 vs 16:30, which is wrong for a barbershop/clinic.
  | "datetime"
  // A clock time only (HH:mm), no calendar day. For opening hours / slot times.
  | "time"
  | "enum"
  // A relation: stores the referenced record's id (uuid). `entity` names the
  // target. Filter by it like any field; `?expand=<field>` embeds the related
  // record (see engine). This is how entities link (Task.projectId → Project).
  | "reference";

export interface FieldDef {
  type: FieldType;
  required?: boolean;
  /** Default applied on create when the field is omitted. */
  default?: unknown;
  /** For `type: "enum"` — the allowed values. */
  options?: string[];
  /** For `type: "reference"` — the target entity name. */
  entity?: string;
  /** For `type: "reference"` — what happens to THIS row when the row it points
   *  at is deleted. `"setNull"` (default) clears the dangling pointer so the
   *  child survives without a broken link; `"cascade"` deletes the child too
   *  (a Task whose Project is gone); `"restrict"` blocks the parent delete while
   *  any child still references it. Prevents orphaned references the moment a
   *  parent is removed. Enforced server-side in the engine's deleteRecord. */
  onDelete?: "setNull" | "cascade" | "restrict";
  /** For `type: "number"` — reject values below this (e.g. `0` for a price, so a
   *  −50 000 can't inflate a dashboard total). Enforced server-side AND in the form. */
  min?: number;
  /** For `type: "number"` — reject values above this. */
  max?: number;
  /** For `type: "number"` — the input step (e.g. `0.01` for money, `1` for counts). */
  step?: number;
  /** Reject a create whose value duplicates an existing row's value for this
   *  field (case-insensitive for strings). Stops the "same client entered 3×"
   *  problem. Enforced server-side in the engine. */
  unique?: boolean;
  /** Sensitive field (phone, email, address). On a `public` entity the engine
   *  strips it from reads by anyone who is NOT the row's author/admin — so a
   *  public directory can't leak contacts to anonymous scrapers. Ignored on
   *  `owner`/`admin` entities (only the owner/admin reads those anyway). */
  private?: boolean;
}

/** Who may read/write rows of this entity. */
export type AccessPolicy = "owner" | "public" | "admin";

export interface EntityDef {
  name: string;
  access: AccessPolicy;
  fields: Record<string, FieldDef>;
  /** Named-role gating (multi-role apps: teacher/student/parent, doctor/patient,
   *  manager/agent). When set, only a signed-in user whose `users.role` is in the
   *  list may READ rows — layered ON TOP of `access` (so a `public` entity with
   *  `readRoles` stops being anonymously readable). Empty/absent = `access` alone
   *  decides. Roles are assigned by an admin via the managed admin/users API. */
  readRoles?: string[];
  /** Named-role gating for WRITES (create/update/delete). When set, only a user
   *  whose role is listed may write — e.g. only `teacher` posts Grades, students
   *  read them. Layered on top of `access`'s own auth+ownership rules. */
  writeRoles?: string[];
}

/** Entity names must be safe path segments — no traversal, no separators. */
const NAME_RE = /^[A-Za-z][A-Za-z0-9_]*$/;

const ENTITIES_DIR = path.join(process.cwd(), "entities");

// mtime-keyed cache: skip re-reading a file whose mtime hasn't changed.
const cache = new Map<string, { mtimeMs: number; def: EntityDef }>();

function entityPath(name: string): string {
  return path.join(ENTITIES_DIR, `${name}.json`);
}

/**
 * Load one entity definition, or null if it doesn't exist / is invalid.
 * Re-reads from disk only when the file changed (mtime), so live AI edits are
 * picked up immediately.
 */
export async function loadEntity(name: string): Promise<EntityDef | null> {
  if (!NAME_RE.test(name)) return null;
  let stat: Awaited<ReturnType<typeof fs.stat>>;
  try {
    stat = await fs.stat(entityPath(name));
  } catch {
    return null;
  }
  const cached = cache.get(name);
  if (cached && cached.mtimeMs === stat.mtimeMs) return cached.def;
  try {
    const raw = await fs.readFile(entityPath(name), "utf8");
    const parsed = JSON.parse(raw) as Partial<EntityDef>;
    const def = normalize(name, parsed);
    cache.set(name, { mtimeMs: stat.mtimeMs, def });
    return def;
  } catch {
    return null;
  }
}

/** List every defined entity name (files in entities/, sans .json). */
export async function listEntities(): Promise<string[]> {
  try {
    const files = await fs.readdir(ENTITIES_DIR);
    return files
      .filter((f) => f.endsWith(".json"))
      .map((f) => f.slice(0, -5))
      .filter((n) => NAME_RE.test(n));
  } catch {
    return [];
  }
}

/** Coerce a loose JSON object into a valid EntityDef (defensive defaults). */
function normalize(name: string, raw: Partial<EntityDef>): EntityDef {
  const access: AccessPolicy =
    raw.access === "public" || raw.access === "admin" ? raw.access : "owner";
  const fields: Record<string, FieldDef> = {};
  for (const [key, f] of Object.entries(raw.fields ?? {})) {
    if (!NAME_RE.test(key)) continue;
    const type = (f?.type ?? "string") as FieldType;
    const num = (v: unknown): number | undefined =>
      typeof v === "number" && Number.isFinite(v) ? v : undefined;
    fields[key] = {
      type,
      required: Boolean(f?.required),
      default: f?.default,
      options: Array.isArray(f?.options) ? f.options : undefined,
      entity:
        type === "reference" && typeof f?.entity === "string" && NAME_RE.test(f.entity)
          ? f.entity
          : undefined,
      onDelete:
        type === "reference" &&
        (f?.onDelete === "cascade" || f?.onDelete === "restrict" || f?.onDelete === "setNull")
          ? f.onDelete
          : undefined,
      min: type === "number" ? num(f?.min) : undefined,
      max: type === "number" ? num(f?.max) : undefined,
      step: type === "number" ? num(f?.step) : undefined,
      unique: Boolean(f?.unique),
      private: Boolean(f?.private),
    };
  }
  // Role lists: keep only safe role-name strings, drop empties → undefined so a
  // bare `[]` never accidentally locks every reader out.
  const roleList = (v: unknown): string[] | undefined => {
    if (!Array.isArray(v)) return undefined;
    const out = v
      .filter((r): r is string => typeof r === "string" && r.trim().length > 0)
      .map((r) => r.trim());
    return out.length ? out : undefined;
  };
  return {
    name,
    access,
    fields,
    readRoles: roleList(raw.readRoles),
    writeRoles: roleList(raw.writeRoles),
  };
}

function zodForField(f: FieldDef): z.ZodTypeAny {
  switch (f.type) {
    case "number": {
      let n = z.number();
      // Range guards (when declared) reject bad input server-side — a negative
      // price or out-of-range count never reaches the store to skew a total.
      if (f.min !== undefined) n = n.min(f.min, { message: `минимум ${f.min}` });
      if (f.max !== undefined) n = n.max(f.max, { message: `максимум ${f.max}` });
      return n;
    }
    case "boolean":
      return z.boolean();
    case "enum":
      return f.options && f.options.length
        ? z.enum(f.options as [string, ...string[]])
        : z.string();
    case "date":
    case "datetime":
      // Stored as an ISO string in JSONB; validate it parses. `datetime` keeps
      // the time-of-day the form sends (…T14:30); `date` is just the day.
      return z.string().refine((s) => !Number.isNaN(Date.parse(s)), {
        message: "invalid date",
      });
    case "time":
      // Clock time only, HH:mm (optionally :ss). Distinct 10:00 vs 16:30 values.
      return z.string().refine((s) => /^\d{2}:\d{2}(:\d{2})?$/.test(s), {
        message: "invalid time",
      });
    case "reference":
      // Stores the referenced record's id (uuid string).
      return z.string().min(1);
    case "string":
    case "text":
    default:
      return z.string();
  }
}

/** Reference fields → their target entity name (for ?expand). */
export function referenceFields(def: EntityDef): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, f] of Object.entries(def.fields)) {
    if (f.type === "reference" && f.entity) out[key] = f.entity;
  }
  return out;
}

/**
 * zod schema for a CREATE payload: required fields enforced, optional fields
 * optional, unknown keys stripped. Defaults are applied separately (applyDefaults)
 * so the same builder serves update (partial) cleanly.
 */
export function createSchema(def: EntityDef) {
  const shape: z.ZodRawShape = {};
  for (const [key, f] of Object.entries(def.fields)) {
    const base = zodForField(f);
    // A field with a `default` is always satisfiable: applyDefaults() fills it
    // when omitted (engine.createRecord runs that AFTER this validation). If we
    // still hard-required it here, omitting it would 400 before the default ever
    // applied — making the default dead. So a required field counts as required
    // in the payload only when it has NO default to fall back on.
    const requiredInPayload = f.required && f.default === undefined;
    shape[key] = requiredInPayload ? base : base.optional();
  }
  return z.object(shape).strip();
}

/** zod schema for an UPDATE payload — every field optional. */
export function updateSchema(def: EntityDef) {
  const shape: z.ZodRawShape = {};
  for (const [key, f] of Object.entries(def.fields)) {
    shape[key] = zodForField(f).optional();
  }
  return z.object(shape).strip();
}

/** Fill omitted fields that declare a `default` (create only). */
export function applyDefaults(
  def: EntityDef,
  data: Record<string, unknown>,
): Record<string, unknown> {
  const out = { ...data };
  for (const [key, f] of Object.entries(def.fields)) {
    if (out[key] !== undefined || f.default === undefined) continue;
    // A `default` is part of the SCHEMA, but createSchema validates the PAYLOAD —
    // an omitted field is never seen, so its default is injected here AFTER
    // validation and would otherwise bypass every type/enum check. A bad default
    // (writer typo: enum value not in `options`, a string on a `number`/`boolean`)
    // would then be stored silently — the very value the engine 400s when it is
    // *provided* (createSchema rejects it). Worse, a non-numeric default in a
    // number field 500s any numeric sort (`::numeric`, no safe cast). So run the
    // default through the field's OWN validator and inject it only if it passes;
    // a malformed default is dropped (the field stays omitted) rather than
    // poisoning the row. A VALID default is injected unchanged — this can only
    // turn a silently-stored-invalid into a not-stored, never the reverse.
    if (zodForField(f).safeParse(f.default).success) out[key] = f.default;
  }
  return out;
}

/** Field names safe to filter/sort on, with the SQL cast their type needs. */
export function fieldSqlType(
  def: EntityDef,
  field: string,
): "number" | "date" | "text" | null {
  const f = def.fields[field];
  if (!f) return null;
  if (f.type === "number") return "number";
  // `datetime` casts to timestamptz like `date` so sort/filter order is real
  // chronological order; `time` (HH:mm) falls through to text, which sorts right.
  if (f.type === "date" || f.type === "datetime") return "date";
  return "text";
}
