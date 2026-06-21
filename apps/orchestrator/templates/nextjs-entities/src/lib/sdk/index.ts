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

import { signIn as nextAuthSignIn, signOut as nextAuthSignOut } from "next-auth/react";

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
  /** List EVERY matching row. With no explicit `limit`/`page`/`offset` the SDK
   *  walks the server's pages for you, so the list/search/sort/export/count you
   *  build over the result sees the whole table — never a silent first-50 slice.
   *  Pass `limit`/`page`/`offset` to opt into a single bounded page instead.
   *  Resolves to `[]` on failure (never rejects) — see `safeCollection`. */
  list(params?: ListParams): Promise<Row[]>;
  /** List rows matching exact field values, e.g. `{ done: false }`. Returns the
   *  COMPLETE matching set (auto-paginated) unless `params` pins a page. Resolves
   *  to `[]` on failure (never rejects). */
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

/**
 * Collection reads degrade gracefully: a failed `list`/`filter` (session lost,
 * API hiccup, network blip) resolves to `[]` instead of rejecting. This single
 * rule is what keeps a generated dashboard or table from rendering a
 * permanently blank screen — the common hand-rolled
 * `Promise.all([list, list]).then(setLoading(false))` never catches, so if a
 * read threw, `loading` would stay `true` forever and `if (loading) return
 * null` would paint nothing. With this, the promise always settles: the page
 * clears `loading`, paints its shell, and shows its empty states. Writes
 * (`create`/`update`/`delete`) and `get` still throw — a caller must know when
 * a mutation, or a specific record lookup, failed.
 */
async function safeCollection(p: Promise<Row[]>): Promise<Row[]> {
  try {
    return await p;
  } catch (err) {
    if (typeof console !== "undefined") {
      console.warn("[omnia sdk] collection read failed — rendering empty:", err);
    }
    return [];
  }
}

/** Page size for auto-pagination — mirrors the engine's MAX_LIMIT so each walk
 *  step pulls as much as the server allows. */
const AUTO_PAGE = 500;
/** Hard ceiling on an auto-paginated fetch: never load more than this many rows
 *  into the browser, so a pathological table degrades to "a lot" rather than
 *  hanging the tab. Far above any real single-tenant list. */
const AUTO_FETCH_CAP = 10000;

/** True when the caller pinned the window themselves (explicit paging). Then we
 *  honour exactly one request; otherwise we auto-complete the result set. */
function isPinnedPage(p?: ListParams | Query): boolean {
  if (!p) return false;
  const r = p as Record<string, unknown>;
  return r.limit != null || r.page != null || r.offset != null;
}

/** Walk the server's offset pages until it returns a short page (or we hit the
 *  safety cap), concatenating into one array. Because every page request carries
 *  the same sort/order/filters, the concatenation stays globally ordered — so a
 *  search/sort/export/count built over the result is correct for the WHOLE table,
 *  not just its first page. This is the fix for the "stuck at 50 records" ceiling. */
async function fetchAll(base: string, params?: ListParams | Query): Promise<Row[]> {
  const out: Row[] = [];
  for (let offset = 0; offset < AUTO_FETCH_CAP; offset += AUTO_PAGE) {
    const page = await req<Row[]>(
      "GET",
      base + qs({ ...params, limit: AUTO_PAGE, offset }),
    );
    out.push(...page);
    if (page.length < AUTO_PAGE) break;
  }
  return out;
}

function entityClient(name: string): EntityClient {
  const base = `/api/entities/${encodeURIComponent(name)}`;
  return {
    list: (params) =>
      safeCollection(
        isPinnedPage(params)
          ? req<Row[]>("GET", base + qs(params))
          : fetchAll(base, params),
      ),
    filter: (query, params) => {
      const merged = { ...query, ...params };
      return safeCollection(
        isPinnedPage(merged)
          ? req<Row[]>("GET", base + qs(merged))
          : fetchAll(base, merged),
      );
    },
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

/** Email + password sign-in via the Auth.js Credentials provider. Shared by
 *  `auth.signIn` and the tail of `auth.signUp`. Throws ApiError(401) on bad
 *  credentials so callers can `try/catch` like every other SDK call. */
async function signInWithPassword(email: string, password: string): Promise<void> {
  const res = await nextAuthSignIn("credentials", { email, password, redirect: false });
  if (res?.error) {
    throw new ApiError(401, "Неверный email или пароль");
  }
}

/** Credentials, accepted either positionally or as one object — generated UIs
 *  call `auth.signUp(email, password)` AND `auth.signUp({ email, password })`,
 *  so the SDK tolerates both rather than silently sending an `[object Object]`. */
export interface Creds {
  email: string;
  password: string;
  name?: string;
}
function coerceCreds(a: string | Creds, password?: string, name?: string): Creds {
  if (a && typeof a === "object") {
    return { email: a.email, password: a.password, name: a.name ?? name };
  }
  return { email: String(a ?? ""), password: String(password ?? ""), name };
}

export const auth = {
  /** The signed-in user, or null. */
  me: () => req<Me | null>("GET", "/api/auth/me"),

  /** Sign in with email + password (positional OR `{ email, password }`).
   *  Throws ApiError(401) if they don't match. */
  signIn: (a: string | Creds, password?: string): Promise<void> => {
    const c = coerceCreds(a, password);
    return signInWithPassword(c.email, c.password);
  },

  /** Create an email + password account and sign the user in (positional OR
   *  `{ email, password, name? }`). Throws ApiError (409 if the email is taken). */
  signUp: async (a: string | Creds, password?: string, name?: string): Promise<void> => {
    const c = coerceCreds(a, password, name);
    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email: c.email, password: c.password, name: c.name }),
    });
    if (!res.ok) {
      const json = await res.json().catch(() => ({}));
      throw new ApiError(
        res.status,
        (json as { error?: string }).error ?? "Не удалось зарегистрироваться",
      );
    }
    await signInWithPassword(c.email, c.password);
  },

  /** Clear the current session (no redirect — the caller updates its own UI). */
  signOut: (): Promise<unknown> => nextAuthSignOut({ redirect: false }),
};

export interface UploadResult {
  url: string;
  key: string;
  size: number;
  contentType: string;
}

/**
 * Base44-style "Core" integrations — run server-side with hidden credentials.
 * Phase 2a: file storage + email. (invokeLLM / generateImage land later.)
 * Use in client components; both require a signed-in user.
 */
export const integrations = {
  /** Store a File in object storage; returns a public URL. */
  uploadFile: async (file: File): Promise<UploadResult> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/integrations/upload-file", {
      method: "POST",
      credentials: "include",
      body: fd,
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new ApiError(res.status, (json as { error?: string }).error ?? res.statusText);
    }
    return (json as { data: UploadResult }).data;
  },

  /** Send an email (SMTP; stubbed until configured — check result.stubbed). */
  sendEmail: (input: { to: string; subject: string; body: string }) =>
    req<{ sent: boolean; stubbed?: boolean; note?: string }>(
      "POST",
      "/api/integrations/send-email",
      input,
    ),
};

export interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  role: string;
  created_at?: string;
}

/**
 * Admin-only user management — list accounts and assign roles. This is the
 * assignment side of multi-role RBAC: the first signup is `admin` and promotes
 * other accounts to the app's roles (teacher / manager / …). Calls 403 for a
 * non-admin caller. Build an admin «Пользователи» screen on top of these:
 * `admin.listUsers()` for the table, `admin.setUserRole(id, role)` on a picker.
 */
export const admin = {
  /** Every account (id, email, name, role, created_at). Admin-only. */
  listUsers: () => req<AdminUser[]>("GET", "/api/admin/users"),
  /** Set one account's role. Admin-only; can't strip your own admin. */
  setUserRole: (id: string, role: string) =>
    req<AdminUser>("PATCH", `/api/admin/users/${encodeURIComponent(id)}`, { role }),
};

export interface DirectoryUser {
  id: string;
  name: string;
  role: string;
}

/**
 * User directory — the REAL registered people any signed-in user can pick from
 * (message recipient, assignee, class tutor, …). Safe fields only (id, name,
 * role; no email/phone). This is how you reference a PERSON: store the picked
 * `id` in a `string` field and render the picker with `<UserSelect>`. NEVER make
 * a `User` entity — people are auth accounts, and this lists them.
 */
export const users = {
  /** Everyone signed in can pick from (id, name, role). Auth required. */
  directory: () => req<DirectoryUser[]>("GET", "/api/users/directory"),
};

export { ApiError };
