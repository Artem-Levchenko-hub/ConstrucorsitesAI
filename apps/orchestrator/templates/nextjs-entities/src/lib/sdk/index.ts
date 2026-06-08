/**
 * App SDK — the ONLY way generated frontend code touches data. Base44-style:
 * define an entity in entities/<Name>.json, then immediately use it here as
 * `entities.<Name>`. No per-entity client to register — a Proxy maps any name
 * to its REST endpoint.
 *
 * USE IN CLIENT COMPONENTS ("use client"): calls go to the same-origin
 * `/api/entities/*` routes with the session cookie attached, where the engine
 * enforces auth + ownership. Fetch from a server component to your own API has
 * no cookie/base-url, so do data work in client components (load in `useEffect`
 * or an event handler).
 *
 * Examples:
 *   import { entities, auth } from "@/lib/sdk";
 *   const tasks = await entities.Task.list({ sort: "due", order: "asc" });
 *   const open  = await entities.Task.filter({ done: false });
 *   const t     = await entities.Task.create({ title: "Купить молоко" });
 *   await entities.Task.update(t.id, { done: true });
 *   await entities.Task.delete(t.id);
 *   const me    = await auth.me();
 *
 * Fixed template file — AI never edits it.
 */

export type Row = Record<string, unknown> & {
  id: string;
  /** Related records embedded via `expand` — keyed by the reference field
   *  (e.g. row._expanded?.projectId is the full Project, or null). */
  _expanded?: Record<string, Row | null>;
};
export type Query = Record<string, string | number | boolean | undefined>;
export interface ListParams {
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  page?: number;
  offset?: number;
  /** Embed related records for these reference fields into row._expanded. */
  expand?: string[];
}

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function qs(params?: Query | ListParams): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function req<T = unknown>(
  method: string,
  url: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(url, {
    method,
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, (json as { error?: string }).error ?? res.statusText);
  }
  return (json as { data: T }).data;
}

export interface EntityClient {
  /** List rows. `params`: sort/order/limit/page/offset. */
  list(params?: ListParams): Promise<Row[]>;
  /** List rows matching exact field values, e.g. `{ done: false }`. */
  filter(query: Query, params?: ListParams): Promise<Row[]>;
  /** One row by id. Throws ApiError(404) if missing / not yours.
   *  `params.expand` embeds related records into row._expanded. */
  get(id: string, params?: { expand?: string[] }): Promise<Row>;
  /** Create a row. Validated against the entity schema server-side. */
  create(data: Record<string, unknown>): Promise<Row>;
  /** Partial update by id (merges into existing data). */
  update(id: string, data: Record<string, unknown>): Promise<Row>;
  /** Delete by id. */
  delete(id: string): Promise<{ id: string; deleted: true }>;
}

function entityClient(name: string): EntityClient {
  const base = `/api/entities/${encodeURIComponent(name)}`;
  return {
    list: (params) => req<Row[]>("GET", base + qs(params)),
    filter: (query, params) =>
      req<Row[]>("GET", base + qs({ ...query, ...params })),
    get: (id, params) =>
      req<Row>("GET", `${base}/${encodeURIComponent(id)}${qs(params)}`),
    create: (data) => req<Row>("POST", base, data),
    update: (id, data) => req<Row>("PUT", `${base}/${encodeURIComponent(id)}`, data),
    delete: (id) =>
      req<{ id: string; deleted: true }>(
        "DELETE",
        `${base}/${encodeURIComponent(id)}`,
      ),
  };
}

/** `entities.<EntityName>` → a typed CRUD client for that entity. */
export const entities: Record<string, EntityClient> = new Proxy(
  {} as Record<string, EntityClient>,
  {
    get: (_target, prop: string) => entityClient(prop),
  },
);

export interface Me {
  id: string;
  email: string;
  name?: string | null;
  image?: string | null;
  role: string;
}

export const auth = {
  /** The signed-in user, or null. */
  me: () => req<Me | null>("GET", "/api/auth/me"),
};

export { ApiError };
