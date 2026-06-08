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
  | "enum";

export interface FieldDef {
  type: FieldType;
  required?: boolean;
  /** Default applied on create when the field is omitted. */
  default?: unknown;
  /** For `type: "enum"` — the allowed values. */
  options?: string[];
}

/** Who may read/write rows of this entity. */
export type AccessPolicy = "owner" | "public" | "admin";

export interface EntityDef {
  name: string;
  access: AccessPolicy;
  fields: Record<string, FieldDef>;
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
    fields[key] = {
      type,
      required: Boolean(f?.required),
      default: f?.default,
      options: Array.isArray(f?.options) ? f.options : undefined,
    };
  }
  return { name, access, fields };
}

function zodForField(f: FieldDef): z.ZodTypeAny {
  switch (f.type) {
    case "number":
      return z.number();
    case "boolean":
      return z.boolean();
    case "enum":
      return f.options && f.options.length
        ? z.enum(f.options as [string, ...string[]])
        : z.string();
    case "date":
      // Stored as an ISO string in JSONB; validate it parses.
      return z.string().refine((s) => !Number.isNaN(Date.parse(s)), {
        message: "invalid date",
      });
    case "string":
    case "text":
    default:
      return z.string();
  }
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
    shape[key] = f.required ? base : base.optional();
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
    if (out[key] === undefined && f.default !== undefined) out[key] = f.default;
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
  if (f.type === "date") return "date";
  return "text";
}
