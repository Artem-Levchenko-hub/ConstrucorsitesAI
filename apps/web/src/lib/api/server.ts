/**
 * Server-side fetch helper for React Server Components.
 *
 * The browser-side `apiFetch` from ./client uses `credentials: "include"`
 * which is meaningless on the Node side — server fetches don't see the
 * user's browser cookies automatically. RSCs must read `omnia_session`
 * via `next/headers` and pass it explicitly.
 */

import { cookies } from "next/headers";

const COOKIE_NAME = "omnia_session";

function apiBaseUrl(): string {
  return (
    process.env.INTERNAL_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:8000"
  );
}

/**
 * Status-aware server fetch. Lets callers tell apart "you can't have this"
 * (401/403/404 — redirect the user somewhere useful) from "something broke"
 * (5xx / network — `status: 0`), instead of collapsing every failure to one
 * null and stranding the user on a 404. `serverApiFetch` keeps the old
 * null-on-any-error shape for callers that don't need the distinction.
 */
export type ServerFetchResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number };

export async function serverApiFetchResult<T>(
  path: string,
): Promise<ServerFetchResult<T>> {
  const c = await cookies();
  const token = c.get(COOKIE_NAME)?.value;
  if (!token) return { ok: false, status: 401 };

  try {
    const r = await fetch(`${apiBaseUrl()}${path}`, {
      headers: { Cookie: `${COOKIE_NAME}=${token}` },
      cache: "no-store",
    });
    if (!r.ok) return { ok: false, status: r.status };
    return { ok: true, data: (await r.json()) as T };
  } catch {
    // Network error / unreachable api — status 0 marks "transient", not "gone".
    return { ok: false, status: 0 };
  }
}

/**
 * GET an api endpoint with the user's session cookie attached.
 * Returns parsed JSON on 2xx, null on any error or non-2xx — callers decide
 * whether that means notFound() or a degraded render.
 */
export async function serverApiFetch<T>(path: string): Promise<T | null> {
  const res = await serverApiFetchResult<T>(path);
  return res.ok ? res.data : null;
}
